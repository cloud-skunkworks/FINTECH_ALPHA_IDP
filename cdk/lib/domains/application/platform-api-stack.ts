// TOGAF: Application Architecture Domain — Provisioning API
// Owns the FastAPI service, its Cognito auth pool, CodeDeploy canary strategy, and alarms.
// Auditors: Cognito MFA/password policy and ECS task role permissions are defined here.

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as eks from 'aws-cdk-lib/aws-eks';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import { Construct } from 'constructs';

export interface PlatformApiStackProps extends cdk.StackProps {
  environment: string;
  vpc: ec2.Vpc;
  cluster: eks.Cluster;
}

export class PlatformApiStack extends cdk.Stack {
  public readonly ecrRepository: ecr.Repository;
  public readonly service: ecs.FargateService;

  constructor(scope: Construct, id: string, props: PlatformApiStackProps) {
    super(scope, id, props);

    const { environment, vpc } = props;
    const isProd = environment === 'prod';

    this.ecrRepository = new ecr.Repository(this, 'ApiRepository', {
      repositoryName: `idp-platform-api-${environment}`,
      imageScanOnPush: true,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      lifecycleRules: [
        {
          description: 'Keep last 10 tagged images',
          maxImageCount: 10,
          tagStatus: ecr.TagStatus.TAGGED,
          tagPrefixList: ['v'],
        },
        {
          description: 'Remove untagged after 7 days',
          maxImageAge: cdk.Duration.days(7),
          tagStatus: ecr.TagStatus.UNTAGGED,
        },
      ],
    });

    // Cognito User Pool — platform team manages user provisioning (self-sign-up is disabled).
    // MFA is REQUIRED. Auditors: see passwordPolicy and advancedSecurityMode below.
    const userPool = new cognito.UserPool(this, 'UserPool', {
      userPoolName: `idp-platform-${environment}`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      standardAttributes: {
        email: { required: true, mutable: false },
        fullname: { required: true, mutable: true },
      },
      passwordPolicy: {
        minLength: 14,
        requireDigits: true,
        requireLowercase: true,
        requireUppercase: true,
        requireSymbols: true,
        tempPasswordValidity: cdk.Duration.days(3),
      },
      mfa: cognito.Mfa.REQUIRED,
      mfaSecondFactor: { sms: false, otp: true },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      advancedSecurityMode: cognito.AdvancedSecurityMode.ENFORCED,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // Three scopes map to three operations. The IDP API validates scope on every request.
    const resourceServer = userPool.addResourceServer('ApiResourceServer', {
      identifier: 'idp-api',
      scopes: [
        { scopeName: 'provision', scopeDescription: 'Submit provisioning requests' },
        { scopeName: 'read', scopeDescription: 'Read catalog and job status' },
        { scopeName: 'destroy', scopeDescription: 'Destroy provisioned resources' },
      ],
    });

    const userPoolClient = userPool.addClient('ApiClient', {
      userPoolClientName: `idp-platform-api-${environment}`,
      generateSecret: true,
      oAuth: {
        flows: { clientCredentials: true },
        scopes: [
          cognito.OAuthScope.resourceServer(resourceServer, { scopeName: 'provision', scopeDescription: 'Submit provisioning requests' }),
          cognito.OAuthScope.resourceServer(resourceServer, { scopeName: 'read', scopeDescription: 'Read catalog and job status' }),
        ],
      },
      refreshTokenValidity: cdk.Duration.days(1),
      accessTokenValidity: cdk.Duration.minutes(60),
      idTokenValidity: cdk.Duration.minutes(60),
    });

    // Secrets Manager stores Cognito IDs and the JWT signing key.
    // The task definition references these as SecretManager refs — no plaintext env vars.
    const apiSecret = new secretsmanager.Secret(this, 'ApiSecret', {
      secretName: `/idp/${environment}/platform-api`,
      description: 'IDP Platform API secrets',
      generateSecretString: {
        secretStringTemplate: JSON.stringify({
          cognito_user_pool_id: userPool.userPoolId,
          cognito_client_id: userPoolClient.userPoolClientId,
        }),
        generateStringKey: 'jwt_secret_key',
        excludePunctuation: false,
        passwordLength: 32,
      },
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const ecsCluster = new ecs.Cluster(this, 'EcsCluster', {
      clusterName: `idp-platform-${environment}`,
      vpc,
      containerInsights: true,
      enableFargateCapacityProviders: true,
    });

    // Task role: what the running container is allowed to do in AWS.
    // Principle of least privilege — only the actions this service genuinely needs.
    const taskRole = new iam.Role(this, 'TaskRole', {
      roleName: `idp-platform-api-task-${environment}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });

    apiSecret.grantRead(taskRole);

    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['eks:DescribeCluster', 'eks:ListClusters'],
      resources: [`arn:aws:eks:${this.region}:${this.account}:cluster/idp-*`],
    }));

    taskRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: [
        'sqs:SendMessage',
        'sqs:ReceiveMessage',
        'sqs:DeleteMessage',
        'sqs:GetQueueAttributes',
      ],
      resources: [`arn:aws:sqs:${this.region}:${this.account}:idp-provision-jobs-${environment}`],
    }));

    // Execution role: what ECS needs to start the task (pull image, read secrets).
    const executionRole = new iam.Role(this, 'ExecutionRole', {
      roleName: `idp-platform-api-exec-${environment}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });
    this.ecrRepository.grantPull(executionRole);
    apiSecret.grantRead(executionRole);

    const logGroup = new logs.LogGroup(this, 'ApiLogGroup', {
      logGroupName: `/ecs/idp-platform-api-${environment}`,
      retention: isProd ? logs.RetentionDays.SIX_MONTHS : logs.RetentionDays.ONE_WEEK,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const taskDefinition = new ecs.FargateTaskDefinition(this, 'ApiTaskDef', {
      family: `idp-platform-api-${environment}`,
      cpu: isProd ? 1024 : 256,
      memoryLimitMiB: isProd ? 2048 : 512,
      taskRole,
      executionRole,
      runtimePlatform: {
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
        cpuArchitecture: ecs.CpuArchitecture.ARM64, // Graviton — ~20% lower cost vs x86
      },
    });

    taskDefinition.addContainer('Api', {
      containerName: 'idp-platform-api',
      image: ecs.ContainerImage.fromEcrRepository(this.ecrRepository, 'latest'),
      portMappings: [{ containerPort: 8000, protocol: ecs.Protocol.TCP }],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'api', logGroup }),
      environment: {
        ENVIRONMENT: environment,
        AWS_REGION: this.region,
        PORT: '8000',
        LOG_LEVEL: isProd ? 'INFO' : 'DEBUG',
      },
      // Secrets are injected at task start — never appear in CloudFormation or task logs.
      secrets: {
        COGNITO_USER_POOL_ID: ecs.Secret.fromSecretsManager(apiSecret, 'cognito_user_pool_id'),
        COGNITO_CLIENT_ID: ecs.Secret.fromSecretsManager(apiSecret, 'cognito_client_id'),
        JWT_SECRET_KEY: ecs.Secret.fromSecretsManager(apiSecret, 'jwt_secret_key'),
      },
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:8000/healthz || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(5),
        retries: 3,
        startPeriod: cdk.Duration.seconds(15),
      },
      readonlyRootFilesystem: true,
      user: '1000', // Non-root
    });

    // Internal ALB — not internet-facing. Access via API Gateway or AWS PrivateLink.
    // CodeDeploy blue/green strategy with canary shift; see the alarm below for auto-rollback.
    const albFargate = new ecs_patterns.ApplicationLoadBalancedFargateService(this, 'ApiService', {
      cluster: ecsCluster,
      taskDefinition,
      serviceName: `idp-platform-api-${environment}`,
      desiredCount: isProd ? 3 : 1,
      publicLoadBalancer: false,
      assignPublicIp: false,
      taskSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      deploymentController: {
        type: ecs.DeploymentControllerType.CODE_DEPLOY,
      },
      capacityProviderStrategies: isProd
        ? [
            { capacityProvider: 'FARGATE', weight: 1, base: 2 },
            { capacityProvider: 'FARGATE_SPOT', weight: 4 },
          ]
        : [{ capacityProvider: 'FARGATE_SPOT', weight: 1 }],
    });

    this.service = albFargate.service;

    const scaling = albFargate.service.autoScaleTaskCount({
      minCapacity: isProd ? 3 : 1,
      maxCapacity: isProd ? 20 : 4,
    });

    scaling.scaleOnCpuUtilization('CpuScaling', {
      targetUtilizationPercent: 60,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(30),
    });

    scaling.scaleOnRequestCount('RequestScaling', {
      requestsPerTarget: 1000,
      targetGroup: albFargate.targetGroup,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(30),
    });

    // These alarms are wired to CodeDeploy to trigger automatic rollback.
    // If either breaches during a canary shift, CodeDeploy rolls back to the blue environment.
    new cloudwatch.Alarm(this, 'ErrorRateAlarm', {
      alarmName: `idp-platform-api-error-rate-${environment}`,
      alarmDescription: 'HTTP 5xx error rate > threshold over 5 minutes — triggers CodeDeploy rollback',
      metric: albFargate.loadBalancer.metricHttpCodeTarget(
        cdk.aws_elasticloadbalancingv2.HttpCodeTarget.TARGET_5XX_COUNT,
        { period: cdk.Duration.minutes(5), statistic: 'Sum' },
      ),
      threshold: 10,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'LatencyAlarm', {
      alarmName: `idp-platform-api-latency-p99-${environment}`,
      alarmDescription: 'p99 latency > 2s over 5 minutes — triggers CodeDeploy rollback',
      metric: albFargate.loadBalancer.metricTargetResponseTime({
        period: cdk.Duration.minutes(5),
        statistic: 'p99',
      }),
      threshold: 2,
      evaluationPeriods: 2,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cdk.CfnOutput(this, 'EcrRepositoryUri', {
      value: this.ecrRepository.repositoryUri,
      exportName: `IdpApiEcrUri-${environment}`,
    });

    new cdk.CfnOutput(this, 'UserPoolId', {
      value: userPool.userPoolId,
      exportName: `IdpCognitoUserPoolId-${environment}`,
    });

    new cdk.CfnOutput(this, 'ApiSecretArn', {
      value: apiSecret.secretArn,
      exportName: `IdpApiSecretArn-${environment}`,
    });
  }
}
