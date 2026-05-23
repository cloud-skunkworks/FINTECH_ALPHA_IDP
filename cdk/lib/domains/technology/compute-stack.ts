// TOGAF: Technology Architecture Domain — Compute Layer
// Owns the EKS cluster, node groups, managed add-ons, and cluster IAM roles.
// Auditors: IAM roles for nodes and the cluster admin role are defined here.
//           IRSA roles for individual workloads live in lib/constructs/irsa-role.ts.

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface ComputeStackProps extends cdk.StackProps {
  environment: string;
  vpc: ec2.Vpc;
}

export class ComputeStack extends cdk.Stack {
  public readonly cluster: eks.Cluster;
  public readonly clusterAdminRole: iam.Role;
  public readonly nodeRole: iam.Role;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    const { environment, vpc } = props;
    const isProd = environment === 'prod';

    // Assumed by GitHub Actions (via OIDC) and platform engineers (via SSO).
    // See: .github/workflows/cdk-deploy.yml for how the OIDC role is assumed.
    this.clusterAdminRole = new iam.Role(this, 'ClusterAdminRole', {
      roleName: `idp-eks-admin-${environment}`,
      assumedBy: new iam.CompositePrincipal(
        new iam.ServicePrincipal('eks.amazonaws.com'),
        new iam.AccountPrincipal(this.account),
      ),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSClusterPolicy'),
      ],
    });

    const controlPlaneLogGroup = new logs.LogGroup(this, 'ControlPlaneLogGroup', {
      logGroupName: `/aws/eks/idp-${environment}/cluster`,
      retention: isProd ? logs.RetentionDays.SIX_MONTHS : logs.RetentionDays.ONE_MONTH,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // EKS 1.32 — latest stable release as of the time of this refactor.
    // To upgrade: change the version string and re-run cdk diff to review node group changes.
    // Always upgrade control plane before node groups (EKS upgrade docs: https://docs.aws.amazon.com/eks/latest/userguide/update-cluster.html)
    this.cluster = new eks.Cluster(this, 'Cluster', {
      clusterName: `idp-eks-${environment}`,
      version: eks.KubernetesVersion.of('1.32'),
      vpc,
      vpcSubnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
      mastersRole: this.clusterAdminRole,
      defaultCapacity: 0, // All capacity managed by the node groups below
      // Prod: private API server only (kubectl requires VPN or bastion).
      // Non-prod: public+private for convenience during development.
      endpointAccess: isProd
        ? eks.EndpointAccess.PRIVATE
        : eks.EndpointAccess.PUBLIC_AND_PRIVATE,
      clusterLogging: [
        eks.ClusterLoggingTypes.API,
        eks.ClusterLoggingTypes.AUDIT,
        eks.ClusterLoggingTypes.AUTHENTICATOR,
        eks.ClusterLoggingTypes.CONTROLLER_MANAGER,
        eks.ClusterLoggingTypes.SCHEDULER,
      ],
      tags: {
        Environment: environment,
        ManagedBy: 'aws-cdk',
      },
    });

    // Shared node role — grants nodes access to ECR and CloudWatch only.
    // Business permissions (DynamoDB, S3, etc.) go on IRSA roles, NOT here.
    this.nodeRole = new iam.Role(this, 'NodeRole', {
      roleName: `idp-eks-node-${environment}`,
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKSWorkerNodePolicy'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEC2ContainerRegistryReadOnly'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonEKS_CNI_Policy'),
        // Enables AWS Systems Manager Session Manager for node debugging (replaces SSH access)
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });

    // System node group runs kube-system workloads (CoreDNS, kube-proxy, OPA Gatekeeper).
    // Tainted so application pods cannot schedule here — keeps system components isolated.
    this.cluster.addNodegroupCapacity('SystemNodeGroup', {
      nodegroupName: `system-${environment}`,
      instanceTypes: [new ec2.InstanceType('m7i.large')],
      minSize: isProd ? 3 : 1,
      desiredSize: isProd ? 3 : 1,
      maxSize: isProd ? 6 : 3,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      nodeRole: this.nodeRole,
      capacityType: eks.CapacityType.ON_DEMAND,
      diskSize: 50,
      labels: { role: 'system', 'node-group': 'system' },
      taints: [
        {
          effect: eks.TaintEffect.NO_SCHEDULE,
          key: 'CriticalAddonsOnly',
          value: 'true',
        },
      ],
      tags: {
        Name: `idp-eks-system-${environment}`,
        Environment: environment,
        CostCentre: 'CC-0001',
        Owner: 'platform-engineering',
        Project: 'idp-platform',
      },
    });

    // Workload node group handles all developer-provisioned services.
    // Multiple instance types provide capacity flexibility; Spot in non-prod cuts cost.
    this.cluster.addNodegroupCapacity('WorkloadNodeGroup', {
      nodegroupName: `workload-${environment}`,
      instanceTypes: [
        new ec2.InstanceType('m7i.xlarge'),
        new ec2.InstanceType('m7a.xlarge'), // AMD variant for capacity fallback
        new ec2.InstanceType('m7i.2xlarge'), // Larger instances for burst capacity
      ],
      minSize: isProd ? 3 : 1,
      desiredSize: isProd ? 6 : 2,
      maxSize: isProd ? 50 : 10,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      nodeRole: this.nodeRole,
      // Non-prod uses Spot to reduce cost by ~70% — acceptable for dev/uat interruptions.
      // Prod uses On-Demand for guaranteed availability.
      capacityType: isProd ? eks.CapacityType.ON_DEMAND : eks.CapacityType.SPOT,
      diskSize: 100,
      labels: { role: 'workload', 'node-group': 'workload' },
      tags: {
        Name: `idp-eks-workload-${environment}`,
        Environment: environment,
        CostCentre: 'CC-0001',
        Owner: 'platform-engineering',
        Project: 'idp-platform',
      },
    });

    // Managed add-ons: EKS manages the lifecycle of these components.
    // Pin addonVersion in production to prevent unexpected upgrades during cluster updates.
    // To find the latest version for your K8s version:
    //   aws eks describe-addon-versions --kubernetes-version 1.32 --addon-name <name>
    new eks.CfnAddon(this, 'VpcCniAddon', {
      clusterName: this.cluster.clusterName,
      addonName: 'vpc-cni',
      resolveConflicts: 'OVERWRITE',
    });

    new eks.CfnAddon(this, 'CoreDnsAddon', {
      clusterName: this.cluster.clusterName,
      addonName: 'coredns',
      resolveConflicts: 'OVERWRITE',
    });

    new eks.CfnAddon(this, 'KubeProxyAddon', {
      clusterName: this.cluster.clusterName,
      addonName: 'kube-proxy',
      resolveConflicts: 'OVERWRITE',
    });

    new eks.CfnAddon(this, 'EbsCsiAddon', {
      clusterName: this.cluster.clusterName,
      addonName: 'aws-ebs-csi-driver',
      resolveConflicts: 'OVERWRITE',
    });

    // Platform namespace — IDP control-plane pods run here (OTel collector, Gatekeeper).
    this.cluster.addManifest('PlatformNamespace', {
      apiVersion: 'v1',
      kind: 'Namespace',
      metadata: {
        name: 'idp-platform',
        labels: {
          'app.kubernetes.io/managed-by': 'aws-cdk',
          'environment': environment,
        },
      },
    });

    new cdk.CfnOutput(this, 'ClusterName', {
      value: this.cluster.clusterName,
      exportName: `IdpEksClusterName-${environment}`,
    });

    new cdk.CfnOutput(this, 'ClusterArn', {
      value: this.cluster.clusterArn,
      exportName: `IdpEksClusterArn-${environment}`,
    });

    new cdk.CfnOutput(this, 'OidcProviderArn', {
      value: this.cluster.openIdConnectProvider.openIdConnectProviderArn,
      exportName: `IdpEksOidcProviderArn-${environment}`,
    });

    new cdk.CfnOutput(this, 'ClusterAdminRoleArn', {
      value: this.clusterAdminRole.roleArn,
      exportName: `IdpEksAdminRoleArn-${environment}`,
    });
  }
}
