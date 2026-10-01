// TOGAF: Technology Architecture Domain — Compute Layer
// Owns the EKS cluster, node groups, managed add-ons, and cluster IAM roles.
// Auditors: IAM roles for nodes and the cluster admin role are defined here.
//           IRSA roles for individual workloads live in lib/constructs/irsa-role.ts.

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { KubectlV36Layer } from '@aws-cdk/lambda-layer-kubectl-v36';
import { Construct } from 'constructs';
import { IrsaRole } from '../../constructs/irsa-role';

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
      // Only principals in this account (GitHub OIDC deploy role, SSO roles) may assume it.
      // The EKS service role is created separately by the Cluster construct, so the
      // eks.amazonaws.com principal and AmazonEKSClusterPolicy are NOT needed here.
      assumedBy: new iam.AccountPrincipal(this.account),
    });

    // Pre-created so retention is applied; the cluster depends on it (below) so EKS
    // does not create the group first and cause a name conflict.
    const controlPlaneLogGroup = new logs.LogGroup(this, 'ControlPlaneLogGroup', {
      logGroupName: `/aws/eks/idp-${environment}/cluster`,
      retention: isProd ? logs.RetentionDays.SIX_MONTHS : logs.RetentionDays.ONE_MONTH,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // EKS 1.36 — newest version exposed by aws-cdk-lib 2.272 (eks.KubernetesVersion.V1_36).
    // NOTE: EKS only supports in-place control-plane upgrades one minor at a time. An existing
    // cluster on 1.32 must step 1.33 -> 1.34 -> 1.35 -> 1.36 (one deploy each). A fresh deploy is fine.
    // The kubectlLayer major version MUST match the cluster version.
    // Always upgrade control plane before node groups (EKS upgrade docs: https://docs.aws.amazon.com/eks/latest/userguide/update-cluster.html)
    this.cluster = new eks.Cluster(this, 'Cluster', {
      clusterName: `idp-eks-${environment}`,
      version: eks.KubernetesVersion.V1_36,
      kubectlLayer: new KubectlV36Layer(this, 'KubectlLayer'),
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

    this.cluster.node.addDependency(controlPlaneLogGroup);

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
    const systemNodes = this.cluster.addNodegroupCapacity('SystemNodeGroup', {
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
    const workloadNodes = this.cluster.addNodegroupCapacity('WorkloadNodeGroup', {
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
    // Pin versions via cdk.json / -c context key "idp:addonVersions", e.g.
    //   {"vpc-cni":"v1.20.1-eksbuild.1","coredns":"...","kube-proxy":"...","aws-ebs-csi-driver":"..."}
    // Find valid values with:
    //   aws eks describe-addon-versions --kubernetes-version 1.36 --addon-name <name>
    // Prod synth emits a warning for every add-on left unpinned.
    const pins: Record<string, string> = this.node.tryGetContext('idp:addonVersions') ?? {};
    const addon = (id: string, addonName: string, extra: Partial<eks.CfnAddonProps> = {}) => {
      const pinned = pins[addonName];
      if (!pinned && isProd) {
        cdk.Annotations.of(this).addWarning(
          `EKS add-on "${addonName}" is not pinned (context idp:addonVersions) in prod.`,
        );
      }
      const a = new eks.CfnAddon(this, id, {
        clusterName: this.cluster.clusterName,
        addonName,
        addonVersion: pinned,
        resolveConflicts: 'OVERWRITE',
        ...extra,
      });
      a.node.addDependency(workloadNodes);
      a.node.addDependency(systemNodes);
      return a;
    };

    // EBS CSI controller needs an IRSA role, otherwise the add-on goes DEGRADED.
    const ebsCsiRole = new IrsaRole(this, 'EbsCsiIrsa', {
      cluster: this.cluster,
      namespace: 'kube-system',
      serviceAccountName: 'ebs-csi-controller-sa',
      roleName: `idp-ebs-csi-${environment}`,
      policies: [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonEBSCSIDriverPolicy')],
    });

    addon('VpcCniAddon', 'vpc-cni');
    addon('CoreDnsAddon', 'coredns');
    addon('KubeProxyAddon', 'kube-proxy');
    addon('EbsCsiAddon', 'aws-ebs-csi-driver', { serviceAccountRoleArn: ebsCsiRole.roleArn });

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
