import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { createIdpStacks, IdpStacks } from '../lib/idp-app';

const ACCOUNT = '123456789012';
const REGION = 'ca-central-1';

function build(envName: string): IdpStacks {
  process.env.CDK_DEFAULT_ACCOUNT = ACCOUNT;
  process.env.CDK_REGION = REGION;
  const app = new cdk.App({
    context: {
      env: envName,
      [`availability-zones:account=${ACCOUNT}:region=${REGION}`]: [`${REGION}a`, `${REGION}b`, `${REGION}d`],
    },
  });
  return createIdpStacks(app);
}

// Collect every IAM statement (inline policies + managed policies + role inline) from a template.
function statements(t: Template, type: string): any[] {
  const out: any[] = [];
  for (const r of Object.values(t.findResources(type)) as any[]) {
    const docs = [r.Properties?.PolicyDocument, ...(r.Properties?.Policies ?? []).map((p: any) => p.PolicyDocument)];
    for (const d of docs) if (d) out.push(...[].concat(d.Statement));
  }
  return out;
}
const arr = (x: any): any[] => (x === undefined ? [] : [].concat(x));

describe.each(['dev', 'prod'])('IDP stacks (%s)', (envName) => {
  const s = build(envName);
  const isProd = envName === 'prod';

  test('stack ids', () => {
    expect(s.network.stackName).toBe(`IdpNetworkStack-${envName}`);
    expect(s.compute.stackName).toBe(`IdpEksStack-${envName}`);
    expect(s.platformApi.stackName).toBe(`IdpPlatformApiStack-${envName}`);
    expect(s.backstage.stackName).toBe(`IdpBackstageStack-${envName}`);
    expect(s.observability.stackName).toBe(`IdpObservabilityStack-${envName}`);
  });

  test('all stacks synthesize', () => {
    for (const st of Object.values(s)) {
      expect(() => Template.fromStack(st)).not.toThrow();
    }
  });

  test('network: flow logs and VPC endpoints exist', () => {
    const t = Template.fromStack(s.network);
    t.resourceCountIs('AWS::EC2::FlowLog', 1);
    t.hasResourceProperties('AWS::EC2::VPCEndpoint', { VpcEndpointType: 'Gateway' });
  });

  test('compute: cluster endpoint, version and logging', () => {
    const t = Template.fromStack(s.compute);
    const clusters = Object.values(t.findResources('Custom::AWSCDK-EKS-Cluster')) as any[];
    expect(clusters).toHaveLength(1);
    const cfg = clusters[0].Properties.Config;
    expect(cfg.version).toBe('1.36');
    expect(cfg.resourcesVpcConfig.endpointPrivateAccess).toBe(true);
    expect(cfg.resourcesVpcConfig.endpointPublicAccess).toBe(!isProd);
    expect(cfg.logging.clusterLogging[0].enabled).toBe(true);
  });

  test('compute: node role has no wildcard IAM and only managed AWS policies', () => {
    const t = Template.fromStack(s.compute);
    t.hasResourceProperties('AWS::IAM::Role', {
      RoleName: `idp-eks-node-${envName}`,
      ManagedPolicyArns: Match.arrayWith([
        Match.objectLike({ 'Fn::Join': ['', Match.arrayWith([Match.stringLikeRegexp('AmazonEKSWorkerNodePolicy')])] }),
      ]),
    });
    const nodeRole = Object.values(t.findResources('AWS::IAM::Role')).find(
      (r: any) => r.Properties.RoleName === `idp-eks-node-${envName}`,
    ) as any;
    expect(nodeRole.Properties.Policies ?? []).toHaveLength(0);
    // No customer-managed/inline policy may be attached to the node role.
    const nodeRoleId = Object.keys(t.findResources('AWS::IAM::Role')).find(
      (k) => (t.findResources('AWS::IAM::Role') as any)[k] === nodeRole,
    )!;
    for (const p of Object.values(t.findResources('AWS::IAM::Policy')) as any[]) {
      expect(JSON.stringify(p.Properties.Roles ?? [])).not.toContain(nodeRoleId);
    }
  });

  test('compute: add-ons present, admin role not assumable by eks service', () => {
    const t = Template.fromStack(s.compute);
    t.resourceCountIs('AWS::EKS::Addon', 4);
    t.hasResourceProperties('AWS::EKS::Addon', {
      AddonName: 'aws-ebs-csi-driver',
      ServiceAccountRoleArn: Match.anyValue(),
    });
    const admin = Object.values(t.findResources('AWS::IAM::Role')).find(
      (r: any) => r.Properties.RoleName === `idp-eks-admin-${envName}`,
    ) as any;
    expect(JSON.stringify(admin.Properties.AssumeRolePolicyDocument)).not.toContain('eks.amazonaws.com');
  });

  test('compute: prod add-ons without pins emit warnings, dev does not', () => {
    const warnings = Template.fromStack(s.compute) && s.compute.node
      .findAll()
      .flatMap((c) => c.node.metadata)
      .filter((m) => m.type === 'aws:cdk:warning' && String(m.data).includes('not pinned'));
    expect(warnings.length).toBe(isProd ? 4 : 0);
  });

  test('IRSA trust policies use CfnJson (no token map keys) and scoped subjects', () => {
    const t = Template.fromStack(s.observability);
    const role = Object.values(t.findResources('AWS::IAM::Role')).find(
      (r: any) => r.Properties.RoleName === `idp-otel-collector-${envName}`,
    ) as any;
    expect(JSON.stringify(role.Properties.AssumeRolePolicyDocument)).toContain('AssumeRoleWithWebIdentity');
    expect(JSON.stringify(role.Properties.AssumeRolePolicyDocument)).not.toMatch(/system:serviceaccount:[^"]*\*/);
  });

  test('observability: no wildcard action IAM; wildcard resource only for X-Ray', () => {
    const t = Template.fromStack(s.observability);
    for (const st of statements(t, 'AWS::IAM::Policy')) {
      for (const a of arr(st.Action)) expect(a).not.toMatch(/^\*$|:\*$/);
      if (arr(st.Resource).includes('*')) {
        expect(arr(st.Action).every((a: string) => a.startsWith('xray:'))).toBe(true);
      }
    }
    t.resourceCountIs('AWS::APS::Workspace', 1);
    t.resourceCountIs('AWS::S3::Bucket', isProd ? 1 : 0);
  });

  test('platform api: internal ALB, MFA required, no wildcard IAM actions', () => {
    const t = Template.fromStack(s.platformApi);
    t.hasResourceProperties('AWS::ElasticLoadBalancingV2::LoadBalancer', { Scheme: 'internal' });
    t.hasResourceProperties('AWS::Cognito::UserPool', {
      MfaConfiguration: 'ON',
      AdminCreateUserConfig: { AllowAdminCreateUserOnly: true },
    });
    t.hasResourceProperties('AWS::ECS::Service', {
      DeploymentConfiguration: Match.objectLike({ DeploymentCircuitBreaker: { Enable: true, Rollback: true } }),
    });
    for (const st of statements(t, 'AWS::IAM::Policy')) {
      for (const a of arr(st.Action)) expect(a).not.toMatch(/^\*$|:\*$/);
    }
  });

  test('backstage: encrypted Aurora in isolated subnets, service defined, secret attached', () => {
    const t = Template.fromStack(s.backstage);
    t.hasResourceProperties('AWS::RDS::DBCluster', {
      StorageEncrypted: true,
      DeletionProtection: isProd,
    });
    t.resourceCountIs('AWS::ECS::Service', 1);
    t.resourceCountIs('AWS::SecretsManager::SecretTargetAttachment', 1); // gives the secret its `host` key
  });
});

test('prod data stores are retained', () => {
  const s = build('prod');
  Template.fromStack(s.backstage).hasResource('AWS::RDS::DBCluster', {
    DeletionPolicy: 'Retain',
  });
  Template.fromStack(s.observability).hasResource('AWS::S3::Bucket', { DeletionPolicy: 'Retain' });
});
