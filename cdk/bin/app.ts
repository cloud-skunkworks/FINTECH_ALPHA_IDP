#!/usr/bin/env node
// IDP Platform — CDK Application Entry Point
//
// Stack deployment order follows TOGAF architecture domain dependencies:
//
//   TECHNOLOGY DOMAIN          APPLICATION DOMAIN        DATA DOMAIN
//   ─────────────────────      ──────────────────────    ────────────────────
//   NetworkStack               PlatformApiStack          ObservabilityStack
//       │                      BackstageStack
//       └──► ComputeStack
//
// To deploy a single environment:
//   cdk deploy --all -c env=dev
//   cdk deploy --all -c env=prod    # requires GitHub Environment approval gate
//
// To see what will change before deploying:
//   cdk diff --all -c env=dev

import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';

// ── TOGAF: Technology Architecture Domain ─────────────────────────────────────
import { NetworkStack } from '../lib/domains/technology/network-stack';
import { ComputeStack } from '../lib/domains/technology/compute-stack';

// ── TOGAF: Application Architecture Domain ────────────────────────────────────
import { PlatformApiStack } from '../lib/domains/application/platform-api-stack';
import { BackstageStack } from '../lib/domains/application/backstage-stack';

// ── TOGAF: Information Architecture Domain (Data / Observability) ─────────────
import { ObservabilityStack } from '../lib/domains/data/observability-stack';

const app = new cdk.App();

// Account and region are read from environment variables — never hardcoded.
// Set CDK_ACCOUNT_DEV / CDK_ACCOUNT_UAT / CDK_ACCOUNT_PROD in your shell or CI.
const env = (suffix: string): cdk.Environment => ({
  account: process.env[`CDK_ACCOUNT_${suffix.toUpperCase()}`] ?? process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_REGION ?? 'ca-central-1',
});

const targetEnv = app.node.tryGetContext('env') ?? 'dev';

// Mandatory tags applied to every resource in every stack.
// Sentinel policy enforce-tagging blocks resource creation if any of these are missing.
const commonTags: Record<string, string> = {
  Project: 'idp-platform',
  Environment: targetEnv,
  Owner: 'platform-engineering',
  ManagedBy: 'aws-cdk',
};

// ── TECHNOLOGY DOMAIN: Network Layer ──────────────────────────────────────────
// Must deploy first — VPC is a shared dependency for all other stacks.
const networkStack = new NetworkStack(app, `IdpNetworkStack-${targetEnv}`, {
  env: env(targetEnv),
  environment: targetEnv,
  tags: commonTags,
});

// ── TECHNOLOGY DOMAIN: Compute Layer ──────────────────────────────────────────
// Depends on: NetworkStack (VPC)
const computeStack = new ComputeStack(app, `IdpComputeStack-${targetEnv}`, {
  env: env(targetEnv),
  environment: targetEnv,
  vpc: networkStack.vpc,
  tags: commonTags,
});
computeStack.addDependency(networkStack);

// ── APPLICATION DOMAIN: Provisioning API ──────────────────────────────────────
// Depends on: NetworkStack (VPC), ComputeStack (EKS cluster reference)
const platformApiStack = new PlatformApiStack(app, `IdpPlatformApiStack-${targetEnv}`, {
  env: env(targetEnv),
  environment: targetEnv,
  vpc: networkStack.vpc,
  cluster: computeStack.cluster,
  tags: {
    ...commonTags,
    CostCentre: process.env.COST_CENTRE ?? 'CC-0001',
  },
});
platformApiStack.addDependency(computeStack);

// ── APPLICATION DOMAIN: Developer Portal (Backstage) ──────────────────────────
// Depends on: NetworkStack (VPC)
const backstageStack = new BackstageStack(app, `IdpBackstageStack-${targetEnv}`, {
  env: env(targetEnv),
  environment: targetEnv,
  vpc: networkStack.vpc,
  tags: {
    ...commonTags,
    CostCentre: process.env.COST_CENTRE ?? 'CC-0001',
  },
});
backstageStack.addDependency(networkStack);

// ── INFORMATION DOMAIN: Observability ─────────────────────────────────────────
// Depends on: ComputeStack (OIDC provider ARN for the OTel Collector IRSA role)
const observabilityStack = new ObservabilityStack(app, `IdpObservabilityStack-${targetEnv}`, {
  env: env(targetEnv),
  environment: targetEnv,
  vpc: networkStack.vpc,
  eksClusterName: computeStack.cluster.clusterName,
  tags: commonTags,
});
observabilityStack.addDependency(computeStack);

// Apply common tags at the app level as a safety net.
cdk.Tags.of(app).add('Project', 'idp-platform');
cdk.Tags.of(app).add('Environment', targetEnv);
cdk.Tags.of(app).add('ManagedBy', 'aws-cdk');
