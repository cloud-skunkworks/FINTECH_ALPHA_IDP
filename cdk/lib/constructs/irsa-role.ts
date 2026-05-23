// Shared construct used by both platform stacks and every developer-provisioned workload.
// Creates an IAM role whose trust policy is scoped to a single Kubernetes namespace
// + ServiceAccount — the IRSA (IAM Roles for Service Accounts) pattern.
//
// Why IRSA instead of a node instance role?
// With a node role, every pod on that node shares the same AWS permissions — one compromised
// pod can exfiltrate secrets for all other workloads. IRSA binds permissions to a specific
// pod identity, so a breach is contained to that one service.

import * as cdk from 'aws-cdk-lib';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import { Construct } from 'constructs';

export interface IrsaRoleProps {
  /** EKS cluster whose OIDC provider is used for the trust relationship. */
  cluster: eks.Cluster;
  /** Kubernetes namespace the ServiceAccount lives in. */
  namespace: string;
  /** Kubernetes ServiceAccount name — must match the name in your K8s manifest exactly. */
  serviceAccountName: string;
  /** Managed policies to attach to the role. */
  policies?: iam.IManagedPolicy[];
  /** Inline policy statements to add to the role. */
  inlinePolicies?: Record<string, iam.PolicyDocument>;
  /** Human-readable description shown in the IAM console. */
  description?: string;
  /** Override the auto-generated role name (irsa-{namespace}-{serviceAccountName}). */
  roleName?: string;
}

export class IrsaRole extends Construct {
  public readonly role: iam.Role;
  public readonly roleArn: string;

  constructor(scope: Construct, id: string, props: IrsaRoleProps) {
    super(scope, id);

    const {
      cluster,
      namespace,
      serviceAccountName,
      policies = [],
      inlinePolicies = {},
      description,
      roleName,
    } = props;

    const oidcProviderArn = cluster.openIdConnectProvider.openIdConnectProviderArn;

    // IAM trust conditions require the OIDC issuer URL *without* the 'https://' prefix.
    // cluster.clusterOpenIdConnectIssuerUrl is a CloudFormation token (lazy string), so
    // plain string methods like .replace() don't work here.
    // Fn.select(1, Fn.split('https://', url)) evaluates at CloudFormation deploy time
    // and returns everything after 'https://'.
    const oidcIssuer = cdk.Fn.select(
      1,
      cdk.Fn.split('https://', cluster.clusterOpenIdConnectIssuerUrl),
    );

    this.role = new iam.Role(this, 'Role', {
      roleName: roleName ?? `irsa-${namespace}-${serviceAccountName}`,
      description: description ?? `IRSA role for ${namespace}/${serviceAccountName}`,
      assumedBy: new iam.FederatedPrincipal(
        oidcProviderArn,
        {
          // Bind to the exact namespace + ServiceAccount — wildcards are blocked by OPA policy.
          StringEquals: {
            [`${oidcIssuer}:sub`]: `system:serviceaccount:${namespace}:${serviceAccountName}`,
            [`${oidcIssuer}:aud`]: 'sts.amazonaws.com',
          },
        },
        'sts:AssumeRoleWithWebIdentity',
      ),
      managedPolicies: policies,
      inlinePolicies,
      maxSessionDuration: cdk.Duration.hours(1),
    });

    this.roleArn = this.role.roleArn;

    // Output the ARN so engineers can copy it when annotating their K8s ServiceAccount:
    //   eks.amazonaws.com/role-arn: <value>
    new cdk.CfnOutput(this, 'RoleArn', {
      value: this.roleArn,
      description: `IRSA role ARN for ${namespace}/${serviceAccountName}`,
    });
  }

  addToPolicy(statement: iam.PolicyStatement): boolean {
    return this.role.addToPolicy(statement);
  }

  addManagedPolicy(policy: iam.IManagedPolicy): void {
    this.role.addManagedPolicy(policy);
  }
}
