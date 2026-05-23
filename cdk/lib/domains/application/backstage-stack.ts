// TOGAF: Application Architecture Domain — Developer Portal (Backstage)
// Owns the Backstage service catalog, its Aurora Postgres database, and GitHub App credentials.
// Auditors: database encryption, deletion protection, and secrets references are defined here.

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecr from 'aws-cdk-lib/aws-ecr';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as ecs_patterns from 'aws-cdk-lib/aws-ecs-patterns';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as rds from 'aws-cdk-lib/aws-rds';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface BackstageStackProps extends cdk.StackProps {
  environment: string;
  vpc: ec2.Vpc;
}

export class BackstageStack extends cdk.Stack {
  public readonly ecrRepository: ecr.Repository;

  constructor(scope: Construct, id: string, props: BackstageStackProps) {
    super(scope, id, props);

    const { environment, vpc } = props;
    const isProd = environment === 'prod';

    this.ecrRepository = new ecr.Repository(this, 'BackstageRepo', {
      repositoryName: `idp-backstage-${environment}`,
      imageScanOnPush: true,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      lifecycleRules: [
        { maxImageCount: 5, tagStatus: ecr.TagStatus.TAGGED, tagPrefixList: ['v'] },
        { maxImageAge: cdk.Duration.days(7), tagStatus: ecr.TagStatus.UNTAGGED },
      ],
    });

    // Generated password stored in Secrets Manager — never in code or environment variables.
    const dbSecret = new secretsmanager.Secret(this, 'DbSecret', {
      secretName: `/idp/${environment}/backstage-db`,
      generateSecretString: {
        secretStringTemplate: JSON.stringify({ username: 'backstage' }),
        generateStringKey: 'password',
        excludePunctuation: true,
        passwordLength: 32,
      },
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // DB security group — only allows inbound from the ECS task security group on port 5432.
    const dbSg = new ec2.SecurityGroup(this, 'DbSg', {
      vpc,
      description: 'Backstage Aurora Postgres — only accepts connections from the Backstage ECS task',
      allowAllOutbound: false,
    });

    // Aurora Serverless v2 in isolated subnets — no internet route to the database.
    // deletionProtection is enforced in prod to prevent accidental data loss.
    const dbCluster = new rds.DatabaseCluster(this, 'BackstageDb', {
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_2,
      }),
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: isProd ? 16 : 2,
      writer: rds.ClusterInstance.serverlessV2('writer'),
      readers: isProd ? [rds.ClusterInstance.serverlessV2('reader')] : [],
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [dbSg],
      credentials: rds.Credentials.fromSecret(dbSecret),
      databaseName: 'backstage',
      storageEncrypted: true,
      deletionProtection: isProd,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
      backup: {
        retention: isProd ? cdk.Duration.days(30) : cdk.Duration.days(1),
      },
      cloudwatchLogsExports: ['postgresql'],
      cloudwatchLogsRetention: isProd ? logs.RetentionDays.THREE_MONTHS : logs.RetentionDays.ONE_WEEK,
    });

    // GitHub App credentials are provisioned manually and stored in Secrets Manager.
    // The secret must exist before deploying this stack.
    const githubAppSecret = secretsmanager.Secret.fromSecretNameV2(
      this,
      'GithubAppSecret',
      `/idp/${environment}/github-app`,
    );

    const ecsCluster = new ecs.Cluster(this, 'BackstageCluster', {
      clusterName: `idp-backstage-${environment}`,
      vpc,
      containerInsights: true,
    });

    const taskRole = new iam.Role(this, 'TaskRole', {
      roleName: `idp-backstage-task-${environment}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
    });
    dbSecret.grantRead(taskRole);
    githubAppSecret.grantRead(taskRole);

    const executionRole = new iam.Role(this, 'ExecRole', {
      roleName: `idp-backstage-exec-${environment}`,
      assumedBy: new iam.ServicePrincipal('ecs-tasks.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AmazonECSTaskExecutionRolePolicy'),
      ],
    });
    this.ecrRepository.grantPull(executionRole);
    dbSecret.grantRead(executionRole);

    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: `/ecs/idp-backstage-${environment}`,
      retention: isProd ? logs.RetentionDays.THREE_MONTHS : logs.RetentionDays.ONE_WEEK,
      removalPolicy: isProd ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    const taskDef = new ecs.FargateTaskDefinition(this, 'BackstageTaskDef', {
      family: `idp-backstage-${environment}`,
      cpu: isProd ? 2048 : 512,
      memoryLimitMiB: isProd ? 4096 : 1024,
      taskRole,
      executionRole,
    });

    taskDef.addContainer('Backstage', {
      containerName: 'backstage',
      image: ecs.ContainerImage.fromEcrRepository(this.ecrRepository, 'latest'),
      portMappings: [{ containerPort: 7007 }],
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: 'backstage', logGroup }),
      environment: {
        NODE_ENV: isProd ? 'production' : 'development',
        PORT: '7007',
        ENVIRONMENT: environment,
      },
      secrets: {
        POSTGRES_USER: ecs.Secret.fromSecretsManager(dbSecret, 'username'),
        POSTGRES_PASSWORD: ecs.Secret.fromSecretsManager(dbSecret, 'password'),
        POSTGRES_HOST: ecs.Secret.fromSecretsManager(dbSecret, 'host'),
        GITHUB_TOKEN: ecs.Secret.fromSecretsManager(githubAppSecret, 'token'),
      },
      healthCheck: {
        command: ['CMD-SHELL', 'curl -f http://localhost:7007/healthcheck || exit 1'],
        interval: cdk.Duration.seconds(30),
        timeout: cdk.Duration.seconds(10),
        retries: 3,
        startPeriod: cdk.Duration.seconds(30),
      },
      user: '1000',
    });

    // Allow the ECS task to reach the Aurora cluster on the Postgres port.
    const taskSg = new ec2.SecurityGroup(this, 'TaskSg', {
      vpc,
      description: 'Backstage ECS Task SG',
    });
    dbSg.addIngressRule(taskSg, ec2.Port.tcp(5432), 'Backstage task → Aurora Postgres');

    new cdk.CfnOutput(this, 'BackstageEcrUri', {
      value: this.ecrRepository.repositoryUri,
      exportName: `IdpBackstageEcrUri-${environment}`,
    });

    new cdk.CfnOutput(this, 'DbEndpoint', {
      value: dbCluster.clusterEndpoint.hostname,
      exportName: `IdpBackstageDbEndpoint-${environment}`,
    });
  }
}
