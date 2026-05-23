// TOGAF: Technology Architecture Domain — Network Layer
// Provides the foundational VPC topology shared by all compute and application stacks.
// Auditors: this file owns all network boundary controls (subnets, NAT, VPC endpoints, flow logs).

import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export interface NetworkStackProps extends cdk.StackProps {
  environment: string;
}

export class NetworkStack extends cdk.Stack {
  public readonly vpc: ec2.Vpc;
  public readonly privateSubnets: ec2.ISubnet[];
  public readonly publicSubnets: ec2.ISubnet[];
  public readonly isolatedSubnets: ec2.ISubnet[];

  constructor(scope: Construct, id: string, props: NetworkStackProps) {
    super(scope, id, props);

    const { environment } = props;
    const isProd = environment === 'prod';

    // Three-tier subnet layout:
    //   Public  (/24 × 3 AZs) — ALB only, no compute
    //   Private (/22 × 3 AZs) — EKS nodes, ECS tasks, Lambda
    //   Isolated (/24 × 3 AZs) — RDS, ElastiCache; no internet route ever
    this.vpc = new ec2.Vpc(this, 'Vpc', {
      vpcName: `idp-vpc-${environment}`,
      ipAddresses: ec2.IpAddresses.cidr('10.0.0.0/16'),
      maxAzs: 3,
      // Prod: one NAT per AZ for high availability. Non-prod: one shared NAT to cut cost.
      natGateways: isProd ? 3 : 1,
      subnetConfiguration: [
        {
          name: 'Public',
          subnetType: ec2.SubnetType.PUBLIC,
          cidrMask: 24,
          mapPublicIpOnLaunch: false,
        },
        {
          name: 'Private',
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
          cidrMask: 22,
        },
        {
          name: 'Isolated',
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
          cidrMask: 24,
        },
      ],
      enableDnsHostnames: true,
      enableDnsSupport: true,
    });

    this.privateSubnets = this.vpc.privateSubnets;
    this.publicSubnets = this.vpc.publicSubnets;
    this.isolatedSubnets = this.vpc.isolatedSubnets;

    // VPC Flow Logs — all traffic (ACCEPT + REJECT) for PCI-DSS network audit trail
    const flowLogGroup = new logs.LogGroup(this, 'FlowLogGroup', {
      logGroupName: `/aws/vpc/idp-${environment}`,
      retention: isProd ? logs.RetentionDays.ONE_YEAR : logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    this.vpc.addFlowLog('FlowLog', {
      destination: ec2.FlowLogDestination.toCloudWatchLogs(flowLogGroup),
      trafficType: ec2.FlowLogTrafficType.ALL,
    });

    // VPC Endpoints keep traffic on the AWS backbone — avoids NAT data charges and
    // prevents data from traversing the public internet.
    this.vpc.addGatewayEndpoint('S3Endpoint', {
      service: ec2.GatewayVpcEndpointAwsService.S3,
      subnets: [{ subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS }],
    });

    // ECR endpoints are required for EKS nodes in private subnets to pull images.
    this.vpc.addInterfaceEndpoint('EcrApiEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    this.vpc.addInterfaceEndpoint('EcrDockerEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    // SSM enables ECS Exec and Parameter Store without public internet access.
    this.vpc.addInterfaceEndpoint('SsmEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    this.vpc.addInterfaceEndpoint('SsmMessagesEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    this.vpc.addInterfaceEndpoint('SecretsManagerEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    this.vpc.addInterfaceEndpoint('CloudWatchLogsEndpoint', {
      service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      privateDnsEnabled: true,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });

    new cdk.CfnOutput(this, 'VpcId', {
      value: this.vpc.vpcId,
      exportName: `IdpVpcId-${environment}`,
    });

    new cdk.CfnOutput(this, 'PrivateSubnetIds', {
      value: this.vpc.privateSubnets.map(s => s.subnetId).join(','),
      exportName: `IdpPrivateSubnetIds-${environment}`,
    });

    new cdk.CfnOutput(this, 'IsolatedSubnetIds', {
      value: this.vpc.isolatedSubnets.map(s => s.subnetId).join(','),
      exportName: `IdpIsolatedSubnetIds-${environment}`,
    });
  }
}
