"""
Ops Agent — Alert-triggered CloudWatch triage and Slack dispatch.

Triggered by PagerDuty / CloudWatch alarm SNS → Lambda → this agent.
Queries CloudWatch Logs Insights, correlates recent deployments,
and posts a structured triage report to Slack.

Security constraints:
- READ-ONLY: cloudwatch:GetMetricData, logs:StartQuery, logs:GetQueryResults
- CANNOT: modify infrastructure, restart services, or escalate permissions
- All I/O logged to audit S3 bucket
"""

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anthropic
import boto3
import structlog

log = structlog.get_logger(__name__)

client = anthropic.Anthropic()

SYSTEM_PROMPT = Path(__file__).parent / "prompts" / "triage_alert.md"
MODEL = "claude-sonnet-4-6"

_REGION = os.environ.get("AWS_REGION", "ca-central-1")


@dataclass
class TriageReport:
    alarm_name: str
    severity: str
    hypothesis: str
    evidence: list[str]
    recent_deployments: list[dict]
    recommended_actions: list[str]
    escalate: bool
    slack_message: str


def triage_alert(
    alarm_name: str,
    alarm_description: str,
    namespace: str,
    affected_service: str,
    environment: str = "prod",
) -> TriageReport:
    """
    Triage a CloudWatch alarm by querying logs and correlating deployments.

    Args:
        alarm_name: CloudWatch alarm name.
        alarm_description: Human-readable alarm description.
        namespace: Kubernetes namespace or ECS cluster name.
        affected_service: Service name (for log queries).
        environment: Deployment environment.

    Returns:
        TriageReport with hypothesis, evidence, and recommended actions.
    """
    # Gather evidence — READ-ONLY AWS calls
    recent_errors = _query_recent_errors(affected_service, environment)
    recent_deployments = _get_recent_deployments(affected_service)
    metrics_snapshot = _get_metrics_snapshot(alarm_name, namespace)

    system_prompt = SYSTEM_PROMPT.read_text()

    user_message = f"""Triage this production alert and provide a structured diagnosis.

ALARM: {alarm_name}
DESCRIPTION: {alarm_description}
SERVICE: {affected_service}
ENVIRONMENT: {environment}
NAMESPACE: {namespace}

RECENT ERROR LOGS (last 15 min):
{json.dumps(recent_errors, indent=2)}

RECENT DEPLOYMENTS (last 2 hours):
{json.dumps(recent_deployments, indent=2)}

METRICS SNAPSHOT:
{json.dumps(metrics_snapshot, indent=2)}

Return ONLY valid JSON:
{{
  "hypothesis": "Most likely root cause in 1-2 sentences",
  "evidence": ["evidence point 1", "evidence point 2"],
  "deployment_correlated": true | false,
  "recommended_actions": [
    "Specific action 1 (e.g. kubectl rollout undo deployment/X -n Y)",
    "Specific action 2"
  ],
  "escalate": true | false,
  "severity_assessment": "SEV-1" | "SEV-2" | "SEV-3",
  "slack_summary": "One line for Slack: what happened and what to do"
}}
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text
    data = json.loads(raw)

    log.info(
        "ops_agent.triage.complete",
        alarm=alarm_name,
        hypothesis=data.get("hypothesis", "")[:100],
        escalate=data.get("escalate"),
    )

    return TriageReport(
        alarm_name=alarm_name,
        severity=data.get("severity_assessment", "SEV-2"),
        hypothesis=data["hypothesis"],
        evidence=data.get("evidence", []),
        recent_deployments=recent_deployments,
        recommended_actions=data.get("recommended_actions", []),
        escalate=data.get("escalate", False),
        slack_message=data.get("slack_summary", alarm_name),
    )


def _query_recent_errors(service: str, environment: str) -> list[dict]:
    """Query CloudWatch Logs Insights for recent errors — READ-ONLY."""
    logs = boto3.client("logs", region_name=_REGION)
    log_group = f"/idp/workloads/{environment}/{service}"

    query = f"""
fields @timestamp, @message, statusCode, requestId
| filter @message like /ERROR/ or statusCode >= 500
| sort @timestamp desc
| limit 20
"""

    try:
        start_query = logs.start_query(
            logGroupName=log_group,
            startTime=int(time.time()) - 900,  # Last 15 min
            endTime=int(time.time()),
            queryString=query,
        )
        query_id = start_query["queryId"]

        # Poll for results (max 10s)
        for _ in range(10):
            time.sleep(1)
            result = logs.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break

        return [
            {field["field"]: field["value"] for field in row}
            for row in result.get("results", [])
        ]
    except Exception as e:
        log.warning("ops_agent.logs_query.failed", service=service, error=str(e))
        return [{"error": f"Could not query logs: {e}"}]


def _get_recent_deployments(service: str) -> list[dict]:
    """Get recent CodeDeploy deployments — READ-ONLY."""
    codedeploy = boto3.client("codedeploy", region_name=_REGION)
    try:
        response = codedeploy.list_deployments(
            applicationName=service,
            createTimeRange={
                "start": time.time() - 7200,  # Last 2 hours
                "end": time.time(),
            },
        )
        deployments = []
        for dep_id in response.get("deployments", [])[:5]:
            dep = codedeploy.get_deployment(deploymentId=dep_id)["deploymentInfo"]
            deployments.append({
                "deployment_id": dep_id,
                "status": dep.get("status"),
                "created_at": str(dep.get("createTime")),
                "completed_at": str(dep.get("completeTime")),
                "description": dep.get("description", ""),
            })
        return deployments
    except Exception as e:
        log.warning("ops_agent.deployments.failed", service=service, error=str(e))
        return []


def _get_metrics_snapshot(alarm_name: str, namespace: str) -> dict[str, Any]:
    """Fetch recent metric values for the alarm — READ-ONLY."""
    cw = boto3.client("cloudwatch", region_name=_REGION)
    try:
        alarms = cw.describe_alarms(AlarmNames=[alarm_name])
        if not alarms["MetricAlarms"]:
            return {}
        alarm = alarms["MetricAlarms"][0]
        return {
            "state": alarm.get("StateValue"),
            "state_reason": alarm.get("StateReason"),
            "threshold": alarm.get("Threshold"),
            "metric": alarm.get("MetricName"),
            "statistic": alarm.get("Statistic"),
            "evaluation_periods": alarm.get("EvaluationPeriods"),
        }
    except Exception as e:
        log.warning("ops_agent.metrics.failed", alarm=alarm_name, error=str(e))
        return {}
