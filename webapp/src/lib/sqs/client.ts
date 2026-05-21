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
  const region = process.env.AWS_REGION;
  const queueUrl = process.env.SQS_QUEUE_URL;
  const endpoint = process.env.SQS_ENDPOINT_URL;
  const accessKeyId = process.env.AWS_ACCESS_KEY_ID;
  const secretAccessKey = process.env.AWS_SECRET_ACCESS_KEY;

  if (!region) {
    throw new Error(
      "AWS_REGION environment variable is required for SQS client"
    );
  }

  if (!queueUrl) {
    throw new Error(
      "SQS_QUEUE_URL environment variable is required for SQS client"
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
