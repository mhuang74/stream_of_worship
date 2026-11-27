# Allow multiple Provider Attempts per Glue Job

ADR 0017 supersedes the provider order and hedging decisions below: the cascade is now `cascade-simdps` (RunPod, then Modal, then ElevenLabs) with strictly sequential failover. The durability rules here — one submission per backend, first verified result wins, durable intent and winner — still apply.

One Glue Job authorizes ElevenLabs followed by SimDPS on Modal, with RunPod starting when Modal fails or exceeds its hedge threshold. This supersedes the single-provider and ambiguous-redelivery rules in ADR 0014 for cascade jobs. Each backend gets at most one submission and the first verified result supplies the Job's one Glue Take.

Overlapping L4 calls can increase provider spend when both sample before cancellation takes effect. Keeping Modal eligible after starting RunPod reduces the wait when queueing or startup is slow. The Job consumes one user quota slot at first provider contact, and the worker records backend intent, deadlines, and the verified winner so redelivery or rollback can recover the same work. An ambiguous ElevenLabs compose can still incur a provider charge.
