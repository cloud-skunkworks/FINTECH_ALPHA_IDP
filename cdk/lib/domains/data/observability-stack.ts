// TOGAF: Information Architecture Domain — Data / Observability Layer
// Owns Amazon Managed Prometheus, CloudWatch log groups, the OTel Collector IRSA role,
// the long-term log archive bucket (prod), and the CloudWatch health dashboard.
// Auditors: log retention periods, IRSA permissions for the collector, and PAN masking
//           are all enforced through this stack and the OTel Collector config in otel/.

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as amp from 'aws-cdk-lib/aws-aps';
import { Construct } from 'constructs';

export interface ObservabilityStackProps extends cdk.StackProps {
  environment: string;
  vpc: ec2.Vpc;
  eksClusterName: string;
}

export class ObservabilityStack extends cdk.Stack {
  public readonly ampWorkspace: amp.CfnWorkspace;
  public readonly otelCollectorRole: iam.Role;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);

    const { environment, eksClusterName } = props;
    const isProd = environment === 'prod';

    this.ampWorkspace = new amp.CfnWorkspace(this, 'AmpWorkspace', {
      alias: `idp-platform-${environment}`,
      tags: [
        { key: 'Environment', value: environment },
        { key: 'Project', value: 'idp-platform' },
        { key: 'Owner', value: 'platform-engineering' },
      ],
    });

    // The OTel Collector runs as a DaemonSet in the 'monitoring' namespace and uses
    // this IRSA role to remote-write metrics to AMP and ship logs to CloudWatch.
    // cdk.Fn.importValue reads the OIDC provider ARN exported by ComputeStack.
    //
    // Why Fn.split here: IAM trust conditions require the issuer URL *without* 'https://'.
    // Since the OIDC ARN is a CloudFormation token (not a plain string at synth time),
    // we use Fn.select(1, Fn.split('https://', ...)) to strip the protocol prefix.
    const oidcProviderArn = cdk.Fn.importValue(`IdpEksOidcProviderArn-${environment}`);
    const oidcIssuer = cdk.Fn.select(
      1,
      cdk.Fn.split('https://', cdk.Fn.importValue(`IdpEksOidcProviderArn-${environment}`)),
    );

    this.otelCollectorRole = new iam.Role(this, 'OtelCollectorRole', {
      roleName: `idp-otel-collector-${environment}`,
      assumedBy: new iam.FederatedPrincipal(
        oidcProviderArn,
        {
          StringEquals: {
            [`${oidcIssuer}:sub`]: 'system:serviceaccount:monitoring:otel-collector',
            [`${oidcIssuer}:aud`]: 'sts.amazonaws.com',
          },
        },
        'sts:AssumeRoleWithWebIdentity',
      ),
    });

    // Minimal AMP permissions — only remote write and query. No admin or delete.
    this.otelCollectorRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'aps:RemoteWrite',
        'aps:GetSeries',
        'aps:GetLabels',
        'aps:GetMetricMetadata',
      ],
      resources: [this.ampWorkspace.attrArn],
    }));

    // Scoped to /idp/* log groups — the collector cannot write to other teams' groups.
    this.otelCollectorRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'logs:CreateLogGroup',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
        'logs:DescribeLogGroups',
        'logs:DescribeLogStreams',
      ],
      resources: [`arn:aws:logs:${this.region}:${this.account}:log-group:/idp/*`],
    }));

    this.otelCollectorRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'xray:PutTraceSegments',
        'xray:PutTelemetryRecords',
        'xray:GetSamplingRules',
        'xray:GetSamplingTargets',
        'xray:GetSamplingStatisticSummaries',
      ],
      resources: ['*'],
    }));

    const logRetention = isProd ? logs.RetentionDays.ONE_YEAR : logs.RetentionDays.ONE_MONTH;
    const logRemoval = isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY;

    new logs.LogGroup(this, 'PlatformApiLogs', {
      logGroupName: `/idp/${environment}/platform-api`,
      retention: logRetention,
      removalPolicy: logRemoval,
    });

    new logs.LogGroup(this, 'BackstageLogs', {
      logGroupName: `/idp/${environment}/backstage`,
      retention: logRetention,
      removalPolicy: logRemoval,
    });

    new logs.LogGroup(this, 'AgentLogs', {
      logGroupName: `/idp/${environment}/agents`,
      retention: logRetention,
      removalPolicy: logRemoval,
    });

    // Long-term log archive: S3 with lifecycle transitions to IA → Glacier.
    // Required for PCI-DSS audit log retention (12 months minimum).
    if (isProd) {
      const archiveBucket = new s3.Bucket(this, 'LogArchive', {
        bucketName: `idp-log-archive-${this.account}-${environment}`,
        versioned: true,
        encryption: s3.BucketEncryption.S3_MANAGED,
        blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
        enforceSSL: true,
        removalPolicy: cdk.RemovalPolicy.RETAIN,
        lifecycleRules: [
          {
            id: 'TransitionToIA',
            transitions: [
              { storageClass: s3.StorageClass.INFREQUENT_ACCESS, transitionAfter: cdk.Duration.days(30) },
              { storageClass: s3.StorageClass.GLACIER, transitionAfter: cdk.Duration.days(90) },
            ],
          },
        ],
      });

      new cdk.CfnOutput(this, 'LogArchiveBucketName', {
        value: archiveBucket.bucketName,
        exportName: `IdpLogArchiveBucket-${environment}`,
      });
    }

    // CloudWatch dashboard — visible at: CloudWatch → Dashboards → idp-platform-health-{env}
    const dashboard = new cloudwatch.Dashboard(this, 'PlatformDashboard', {
      dashboardName: `idp-platform-health-${environment}`,
      periodOverride: cloudwatch.PeriodOverride.INHERIT,
    });

    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: `# IDP Platform Health — ${environment.toUpperCase()}`,
        width: 24,
        height: 1,
      }),
      new cloudwatch.GraphWidget({
        title: 'Platform API — Request Rate',
        width: 8,
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/ApplicationELB',
            metricName: 'RequestCount',
            dimensionsMap: { LoadBalancer: `app/idp-platform-${environment}` },
            period: cdk.Duration.minutes(5),
            statistic: 'Sum',
          }),
        ],
      }),
      new cloudwatch.GraphWidget({
        title: 'Platform API — Error Rate',
        width: 8,
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/ApplicationELB',
            metricName: 'HTTPCode_Target_5XX_Count',
            dimensionsMap: { LoadBalancer: `app/idp-platform-${environment}` },
            period: cdk.Duration.minutes(5),
            statistic: 'Sum',
          }),
        ],
      }),
      new cloudwatch.GraphWidget({
        title: 'Platform API — p99 Latency',
        width: 8,
        left: [
          new cloudwatch.Metric({
            namespace: 'AWS/ApplicationELB',
            metricName: 'TargetResponseTime',
            dimensionsMap: { LoadBalancer: `app/idp-platform-${environment}` },
            period: cdk.Duration.minutes(5),
            statistic: 'p99',
          }),
        ],
      }),
    );

    new cdk.CfnOutput(this, 'AmpWorkspaceId', {
      value: this.ampWorkspace.attrWorkspaceId,
      exportName: `IdpAmpWorkspaceId-${environment}`,
    });

    new cdk.CfnOutput(this, 'AmpEndpoint', {
      value: this.ampWorkspace.attrPrometheusEndpoint,
      exportName: `IdpAmpEndpoint-${environment}`,
    });

    new cdk.CfnOutput(this, 'OtelCollectorRoleArn', {
      value: this.otelCollectorRole.roleArn,
      exportName: `IdpOtelCollectorRoleArn-${environment}`,
    });
  }
}
