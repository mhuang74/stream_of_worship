import { SQSClient as AWSSQSClient, SendMessageCommand } from "@aws-sdk/client-sqs";

export interface SQSMessage {
  jobId: string;
  songsetId: string;
  userId: number;
}

export interface SQSClientConfig {
  region: string;
  queueUrl: string;
  endpoint?: string;
  accessKeyId?: string;
  secretAccessKey?: string;
}

export class SQSClient {
  private client: AWSSQSClient;
  private queueUrl: string;

  constructor(config: SQSClientConfig) {
    this.queueUrl = config.queueUrl;

    this.client = new AWSSQSClient({
      region: config.region,
      ...(config.endpoint ? { endpoint: config.endpoint } : {}),
      useQueueUrlAsEndpoint: false,
      ...(config.accessKeyId && config.secretAccessKey
        ? {
            credentials: {
              accessKeyId: config.accessKeyId,
              secretAccessKey: config.secretAccessKey,
            },
          }
        : {}),
    });
  }

  async sendMessage(message: SQSMessage): Promise<string> {
    const command = new SendMessageCommand({
      QueueUrl: this.queueUrl,
      MessageBody: JSON.stringify(message),
    });

    const result = await this.client.send(command);
    if (!result.MessageId) {
      throw new Error("Failed to send SQS message: no MessageId returned");
    }
    return result.MessageId;
  }
}

export function createSQSClientFromEnv(): SQSClient {
  const region = process.env.SOW_AWS_REGION;
  const queueUrl = process.env.SOW_SQS_QUEUE_URL;
  const endpoint = process.env.SOW_SQS_ENDPOINT_URL;
  const accessKeyId = process.env.SOW_AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.SOW_AWS_SECRET_ACCESS_KEY;

  if (!region) {
    throw new Error(
      "SOW_AWS_REGION environment variable is required for SQS client"
    );
  }

  if (!queueUrl) {
    throw new Error(
      "SOW_SQS_QUEUE_URL environment variable is required for SQS client"
    );
  }

  return new SQSClient({
    region,
    queueUrl,
    endpoint,
    accessKeyId,
    secretAccessKey,
  });
}

// ---------------------------------------------------------------------------
// Glue queue (spec §2.3 / §6.3)
//
// Glue jobs ride a dedicated queue so a slow provider call can never delay a
// render. The body is kind-tagged; the render message stays untagged and keeps
// going to SOW_SQS_QUEUE_URL. Credentials, region, and endpoint conventions are
// identical to the render client above.
// ---------------------------------------------------------------------------

export interface GlueSQSMessage {
  kind: "glue.generate";
  jobId: string;
  songsetId: string;
  userId: number;
  /** Staging recovery only; ordinary generation omits it. */
  mode?: "staging";
}

export class GlueSQSClient {
  private client: AWSSQSClient;
  private queueUrl: string;

  constructor(config: SQSClientConfig) {
    this.queueUrl = config.queueUrl;

    this.client = new AWSSQSClient({
      region: config.region,
      ...(config.endpoint ? { endpoint: config.endpoint } : {}),
      useQueueUrlAsEndpoint: false,
      ...(config.accessKeyId && config.secretAccessKey
        ? {
            credentials: {
              accessKeyId: config.accessKeyId,
              secretAccessKey: config.secretAccessKey,
            },
          }
        : {}),
    });
  }

  async sendMessage(message: GlueSQSMessage): Promise<string> {
    const command = new SendMessageCommand({
      QueueUrl: this.queueUrl,
      MessageBody: JSON.stringify({
        kind: message.kind,
        jobId: message.jobId,
        songsetId: message.songsetId,
        userId: message.userId,
        ...(message.mode === undefined ? {} : { mode: message.mode }),
      }),
    });

    const result = await this.client.send(command);
    if (!result.MessageId) {
      throw new Error("Failed to send Glue SQS message: no MessageId returned");
    }
    return result.MessageId;
  }
}

export function createGlueSQSClientFromEnv(): GlueSQSClient {
  const region = process.env.SOW_AWS_REGION;
  const queueUrl = process.env.SOW_GLUE_SQS_QUEUE_URL;
  const endpoint = process.env.SOW_SQS_ENDPOINT_URL;
  const accessKeyId = process.env.SOW_AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.SOW_AWS_SECRET_ACCESS_KEY;

  if (!region) {
    throw new Error(
      "SOW_AWS_REGION environment variable is required for Glue SQS client"
    );
  }

  if (!queueUrl) {
    throw new Error(
      "SOW_GLUE_SQS_QUEUE_URL environment variable is required for Glue SQS client"
    );
  }

  return new GlueSQSClient({
    region,
    queueUrl,
    endpoint,
    accessKeyId,
    secretAccessKey,
  });
}
