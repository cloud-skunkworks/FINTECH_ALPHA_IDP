#!/usr/bin/env node
// IDP Platform — CDK application entry point. Stack wiring lives in lib/idp-app.ts so tests can reuse it.
//   cdk deploy --all -c env=dev|uat|prod
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { createIdpStacks } from '../lib/idp-app';

createIdpStacks(new cdk.App());
