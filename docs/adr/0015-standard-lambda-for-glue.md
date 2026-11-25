# Run Glue on standard Lambda

Glue runs on standard on-demand Lambda with a three-minute job deadline. This replaces the Managed Instances hosting decision in ADR 0014. At 500 new jobs per month, usage-based compute can draw on the shared free allowance and avoids idle EC2 costs.

Shorter provider calls can produce more unknown paid outcomes. Persist upload IDs and raw responses before advancing, reuse the existing safe retry rules, and keep ready takes available. Target $5 per month for normal infrastructure usage and notify operators about spend trends. ElevenLabs charges have a separate budget.
