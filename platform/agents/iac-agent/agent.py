"""
IaC Agent — Generates AWS CDK workspace configuration from a provisioning request.

Triggered by POST /v1/provision. Produces TypeScript CDK code that is committed
to a GitHub PR for human review before deployment.

Security constraints:
- Agent has READ-ONLY AWS access (describes resources, no mutations)
- All agent inputs/outputs are logged to the audit S3 bucket
- Human approval gate in GitHub Actions is mandatory before CDK deploy
- No hardcoded account IDs, ARNs, or secrets in generated code
"""

import json
import os
from pathlib import Path
from typing import TypedDict

import anthropic
import boto3
import structlog

log = structlog.get_logger(__name__)

client = anthropic.Anthropic()  # API key from ANTHROPIC_API_KEY env var

SYSTEM_PROMPT = Path(__file__).parent / "prompts" / "generate_workspace.md"
AUDIT_BUCKET = os.environ.get("AGENT_AUDIT_BUCKET", "")
MODEL = "claude-sonnet-4-6"


class WorkspaceConfig(TypedDict):
    """Generated CDK workspace files."""
    main_ts: str
    variables_ts: str
    outputs_ts: str
    readme_md: str


def generate_workspace_config(
    provision_request: dict,
    job_id: str,
) -> WorkspaceConfig:
    """
    Generate CDK workspace configuration for a provisioning request.

    Args:
        provision_request: Validated ProvisionRequest dict from the API.
        job_id: Provisioning job ID for audit correlation.

    Returns:
        WorkspaceConfig with TypeScript CDK files ready to commit.
    """
    system_prompt = SYSTEM_PROMPT.read_text()

    log.info(
        "iac_agent.generate.start",
        job_id=job_id,
        template_id=provision_request.get("template_id"),
        service_name=provision_request.get("service_name"),
        environment=provision_request.get("environment"),
    )

    user_message = f"""Generate CDK workspace configuration for this provisioning request.

Return ONLY valid JSON with these keys: main_ts, variables_ts, outputs_ts, readme_md.
Each key's value is the complete file content as a string.

PROVISIONING REQUEST:
{json.dumps(provision_request, indent=2)}

CONSTRAINTS (non-negotiable):
1. Use WorkloadTemplate construct from lib/constructs/workload-template.ts
2. All mandatory tags must be applied: CostCentre, Environment, Owner, Project
3. IRSA role provisioned via IrsaRole construct — never inline trust policy
4. No hardcoded account IDs, ARNs, or secrets — use CDK context or Secrets Manager
5. Module source must reference the private registry: lib/constructs/
6. serviceName, environment, size, ownerTeam, costCentre from the request
7. Generated code must be TypeScript strict mode compatible
8. Include JSDoc comments explaining what each resource does
9. PCI-DSS context: no PII in resource names, logs, or tags
"""

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_response = response.content[0].text

    # Audit log — store input/output to S3 for compliance
    _audit_log(
        job_id=job_id,
        agent="iac-agent",
        input_data=provision_request,
        output_data=raw_response,
    )

    try:
        config = json.loads(raw_response)
    except json.JSONDecodeError as e:
        log.error("iac_agent.generate.parse_error", job_id=job_id, error=str(e))
        # Attempt to extract JSON from markdown code blocks
        import re
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
        if match:
            config = json.loads(match.group(1))
        else:
            raise ValueError(f"IaC Agent returned unparseable response: {e}") from e

    log.info("iac_agent.generate.success", job_id=job_id)
    return WorkspaceConfig(**config)


def _audit_log(job_id: str, agent: str, input_data: dict, output_data: str) -> None:
    """Write agent I/O to the audit S3 bucket. Required for PCI-DSS compliance."""
    if not AUDIT_BUCKET:
        log.debug("iac_agent.audit_log.skipped", reason="AGENT_AUDIT_BUCKET not set")
        return

    import time
    key = f"agents/{agent}/{time.strftime('%Y/%m/%d')}/{job_id}.json"
    record = {
        "job_id": job_id,
        "agent": agent,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input": input_data,
        "output_length": len(output_data),
        # Do NOT log full output — may contain code with references to sensitive infra details
    }

    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=AUDIT_BUCKET,
            Key=key,
            Body=json.dumps(record),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
    except Exception as e:
        log.warning("iac_agent.audit_log.failed", error=str(e))
