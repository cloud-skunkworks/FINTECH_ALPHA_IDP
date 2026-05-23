// Golden path construct — every developer-provisioned service goes through this.
// Instantiated by the IaC Agent from the Backstage scaffolder output.
//
// What it creates per workload:
//   1. ECR repository (image scanning on push, lifecycle rules)
//   2. IRSA role scoped to the service's namespace/ServiceAccount
//   3. Kubernetes Namespace with Pod Security Admission = restricted
//   4. Kubernetes ServiceAccount annotated with the IRSA role ARN
//   5. CloudWatch Log Group with environment-appropriate retention
//   6. CloudWatch Alarm for memory pressure (> 80% for 3 periods)

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';
import { IrsaRole } from './irsa-role';

export type WorkloadSize = 'xs' | 'sm' | 'md' | 'lg';

// CPU (millicores) and memory (MiB) that map to ECS Fargate task sizes.
const SIZE_MAP: Record<WorkloadSize, { cpu: number; memory: number }> = {
  xs: { cpu: 256, memory: 512 },
  sm: { cpu: 512, memory: 1024 },
  md: { cpu: 1024, memory: 2048 },
  lg: { cpu: 2048, memory: 4096 },
};

export interface WorkloadTemplateProps {
  cluster: eks.Cluster;
  vpc: ec2.Vpc;
  /** Used as the Kubernetes namespace name and the prefix for all AWS resource names. */
  serviceName: string;
  environment: string;
  size: WorkloadSize;
  /** GitHub team slug — used for RBAC bindings and tagging. */
  ownerTeam: string;
  /** Cost centre code, e.g. CC-1234. Required for PCI-DSS cost attribution. */
  costCentre: string;
  /** Additional IAM statements for the workload's IRSA role (DynamoDB, S3, etc.). */
  iamPolicies?: iam.PolicyStatement[];
  minReplicas?: number;
  maxReplicas?: number;
}

export class WorkloadTemplate extends Construct {
  public readonly ecrRepository: ecr.Repository;
  public readonly irsaRole: IrsaRole;
  public readonly logGroup: logs.LogGroup;
  public readonly namespace: string;

  constructor(scope: Construct, id: string, props: WorkloadTemplateProps) {
    super(scope, id);

    const {
      cluster,
      serviceName,
      environment,
      size,
      ownerTeam,
      costCentre,
      iamPolicies = [],
      minReplicas = 1,
      maxReplicas = 10,
    } = props;

    this.namespace = serviceName;
    const isProd = environment === 'prod';

    this.ecrRepository = new ecr.Repository(this, 'EcrRepository', {
      repositoryName: `${serviceName}-${environment}`,
      imageScanOnPush: true,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      lifecycleRules: [
        { maxImageCount: 10, tagStatus: ecr.TagStatus.TAGGED, tagPrefixList: ['v'] },
        { maxImageAge: cdk.Duration.days(7), tagStatus: ecr.TagStatus.UNTAGGED },
      ],
    });

    cdk.Tags.of(this.ecrRepository).add('CostCentre', costCentre);
    cdk.Tags.of(this.ecrRepository).add('Owner', ownerTeam);

    this.irsaRole = new IrsaRole(this, 'IrsaRole', {
      cluster,
      namespace: serviceName,
      serviceAccountName: serviceName,
      description: `IRSA role for ${serviceName} in ${environment}`,
      roleName: `irsa-${serviceName}-${environment}`,
    });

    for (const statement of iamPolicies) {
      this.irsaRole.addToPolicy(statement);
    }

    // Allow the workload to pull its own image and write to its own CloudWatch namespace.
    this.ecrRepository.grantPull(this.irsaRole.role);

    this.irsaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'cloudwatch:PutMetricData',
        'logs:CreateLogStream',
        'logs:PutLogEvents',
      ],
      resources: ['*'],
      // Restrict metric writes to the workload's own namespace — prevents cross-service metric pollution.
      conditions: {
        StringEquals: {
          'cloudwatch:namespace': `IDP/${serviceName}`,
        },
      },
    }));

    // Pod Security Admission = restricted enforces:
    //   - non-root user, no privilege escalation, seccomp profile required
    // OPA Gatekeeper also validates this at admission time (policy/rego/).
    cluster.addManifest(`${serviceName}Namespace`, {
      apiVersion: 'v1',
      kind: 'Namespace',
      metadata: {
        name: serviceName,
        labels: {
          'app.kubernetes.io/managed-by': 'aws-cdk',
          'app.kubernetes.io/part-of': 'idp-platform',
          'environment': environment,
          'owner-team': ownerTeam,
          'cost-centre': costCentre,
          'pod-security.kubernetes.io/enforce': 'restricted',
          'pod-security.kubernetes.io/warn': 'restricted',
        },
        annotations: {
          'idp.internal/cost-centre': costCentre,
          'idp.internal/owner-team': ownerTeam,
          'idp.internal/provisioned-by': 'aws-cdk',
        },
      },
    });

    // The ServiceAccount annotation links the K8s identity to the IRSA IAM role.
    // Without this annotation, pods in this namespace get no AWS permissions.
    cluster.addManifest(`${serviceName}ServiceAccount`, {
      apiVersion: 'v1',
      kind: 'ServiceAccount',
      metadata: {
        name: serviceName,
        namespace: serviceName,
        annotations: {
          'eks.amazonaws.com/role-arn': this.irsaRole.roleArn,
        },
        labels: {
          'app.kubernetes.io/name': serviceName,
          'app.kubernetes.io/managed-by': 'aws-cdk',
        },
      },
    });

    this.logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/idp/workloads/${environment}/${serviceName}`,
      retention: isProd ? logs.RetentionDays.THREE_MONTHS : logs.RetentionDays.ONE_WEEK,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // Memory alarm: if the pod uses > 80% of its limit for 3 consecutive 5-minute periods,
    // PagerDuty is alerted. The Ops Agent also receives this alarm for automated triage.
    new cloudwatch.Alarm(this, 'MemoryAlarm', {
      alarmName: `${serviceName}-${environment}-memory-high`,
      metric: new cloudwatch.Metric({
        namespace: 'ContainerInsights',
        metricName: 'pod_memory_utilization',
        dimensionsMap: {
          ClusterName: cluster.clusterName,
          Namespace: serviceName,
        },
        period: cdk.Duration.minutes(5),
        statistic: 'Average',
      }),
      threshold: 80,
      evaluationPeriods: 3,
      alarmDescription: `Memory utilisation > 80% for ${serviceName} in ${environment}`,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cdk.CfnOutput(this, 'EcrUri', {
      value: this.ecrRepository.repositoryUri,
      description: `ECR URI for ${serviceName}`,
    });

    new cdk.CfnOutput(this, 'IrsaRoleArn', {
      value: this.irsaRole.roleArn,
      description: `IRSA role ARN — annotate the K8s ServiceAccount with this value`,
    });

    new cdk.CfnOutput(this, 'NamespaceOutput', {
      value: serviceName,
      description: `Kubernetes namespace for ${serviceName}`,
    });
  }
}
