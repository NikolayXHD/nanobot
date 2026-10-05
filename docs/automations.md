# Automations

<!-- Meta description: Create, run, and manage nanobot scheduled automations, local triggers, and heartbeat-backed background checks. -->

Automations are agent turns that run later in a linked topic. Use them
when nanobot should do work without someone actively typing: reminders,
recurring checks, nightly summaries, CI follow-ups, local script reports, or
webhook-driven events.

Create automations from the chat channel or WebUI topic where the
result should appear. That lets nanobot keep the right session history,
workspace, and reply target.

## Choose an Automation Type

| Type | Starts from | Best for | Created with |
|---|---|---|---|
| Scheduled automation | Time, interval, or cron expression | Recurring reminders, scheduled summaries, one-time future tasks | Ask nanobot in the target topic to schedule it with the `cron` tool |
| Local trigger | A local `nanobot trigger ...` command | CI jobs, webhooks, shell scripts, generated reports | `/trigger <name>` in the target topic |
| Heartbeat | Protected system schedule | Quiet recurring checks that should only report useful results | Edit `<workspace>/HEARTBEAT.md` |

The two user-created automation types are scheduled automations and local
triggers. Heartbeat uses the same background service but is system-managed and
protected from normal automation edits.

## Before You Create One

Keep `nanobot gateway` running. The gateway owns background delivery for chat
apps, WebUI topics, scheduled automations, local triggers, heartbeat, and
Dream jobs.

Use the same workspace and config for the gateway and any process that sends
local trigger messages. If you run multiple nanobot instances, pass the matching
`--config` or `--workspace` option to `nanobot trigger`.

Create each automation from the target topic. An automation without a linked
topic cannot be enabled or run from the WebUI because nanobot would not know
where to deliver the turn.

## Scheduled Automations

Scheduled automations are created by the agent's `cron` tool. In practice, ask
nanobot from the target chat or WebUI topic:

```text
Every weekday at 9am, check open pull requests and summarize blockers here.
```

or:

```text
Tomorrow at 4pm, remind me to send the release notes.
```

The cron tool supports interval schedules, cron expressions, and one-time
scheduled tasks. Cron expressions can include an IANA timezone such as
`America/Vancouver`; otherwise nanobot uses the runtime default timezone.

Scheduled automations normally deliver the result back to the session where they
were created. Use them for work that should run on a predictable schedule and
report each run.

For background checks that should stay quiet unless there is something useful to
report, use heartbeat instead of a user-created scheduled automation.

## Local Triggers

Local triggers let a local script or external service send a message into a
specific nanobot session later.

Create the trigger from the chat or WebUI topic where future messages should
arrive:

```text
/trigger PR review
```

nanobot replies with a trigger ID and a command shaped like:

```bash
nanobot trigger trg_8K4P2Q9X "Review PR #4502"
```

Replace the quoted text with the message nanobot should receive. For generated
or longer content, pipe stdin:

```bash
generate-report | nanobot trigger trg_8K4P2Q9X
```

For multiple instances, use the same config or workspace selector as the
gateway:

```bash
nanobot trigger --config ./bot-a/config.json trg_8K4P2Q9X "Nightly report"
nanobot trigger --workspace ./bot-a/workspace trg_8K4P2Q9X "Nightly report"
```

nanobot does not provide a built-in public webhook receiver for local triggers.
If GitHub, CI, or another external system should wake nanobot, run your own
small webhook service and have it call `nanobot trigger` after it builds the
final message.

## Heartbeat

Heartbeat is for recurring workspace checks that should usually stay quiet. It
reads `<workspace>/HEARTBEAT.md`, executes active tasks, and sends only useful or
actionable results to the most recently active chat target.

Use heartbeat for checks such as "watch this repo for important failures" or
"periodically inspect this workspace and only tell me when action is needed." Use
a scheduled automation instead when every run should produce a visible reminder
or report.

Heartbeat is enabled by default when `nanobot gateway` starts. Configure it in
[`configuration.md#gateway-heartbeat`](./configuration.md#gateway-heartbeat).

## Manage Automations

Use the WebUI Automations view to:

- filter by all, active, paused, needs-attention, or system jobs;
- search by task name, message, trigger command, linked topic, schedule, or
  status;
- sort by next run, last run, updated time, or name;
- run scheduled automations now;
- pause or resume, rename, or delete user-created automations;
- copy the CLI command for local triggers;
- inspect protected system automations without changing them.

Local triggers do not have a WebUI "Run now" action because each run needs a
message. Copy the `nanobot trigger ...` command from the WebUI and replace
`"message"` with the content that should be delivered.

## Delivery and Reliability

Automation delivery is workspace-local. Scheduled jobs and local trigger
deliveries use the same workspace as the gateway.

Local trigger messages are written to a durable queue. If the gateway is not
running yet, the message waits in that workspace. If the linked topic is
already running a turn, the trigger waits until the session becomes idle instead
of being injected into the active turn.

The local trigger queue is at-least-once, not exactly-once. If the gateway exits
after claiming a delivery but before the linked turn completes, the next gateway
start requeues that delivery. External scripts should make repeated trigger
messages safe. If the delivery reaches the agent and the turn fails, the
delivery is marked failed instead of retrying forever.

Each local trigger delivery writes an audit record under
`<workspace>/triggers/runs`. Run one gateway consumer per workspace; the local
queue is not a distributed multi-consumer queue.

### Prepared message bodies

Goal: the message sent to the user is the contents of a file prepared by a
deterministic script, not the agent reply. The turn still runs the agent: the
agent runs the script, the script writes the message file, and the turn
delivers that file. The session transcript keeps the agent reply.

Why bother:

- The file is rendered verbatim: numbers, dates, and links appear exactly as
  the script wrote them. A model restating the same table paraphrases rows,
  drops entries, and occasionally invents a link.
- A long list (hundreds of rows) does not fit one model reply: the model
  silently shortens it, the script prints every row.

#### Cron job

1. Declare the contract in the job payload, editing
   `<workspace>/cron/jobs.json` directly (the chat cron tool does not expose
   these fields):

   ```json
   {
     "kind": "agent_turn",
     "message": "Prepare the reply inputs. Run foo.py to generate the reply",
     "delivery": "file",
     "bodyFormat": "md_to_html"
   }
   ```

   `bodyFormat: "plaintext"` keeps the body as is. Choose `"md_to_html"` for
   markup-heavy bodies such as tables or preformatted code: the channel
   renders the markdown into a sanitized HTML part. `"html"` treats the body
   as HTML and sanitizes it with the same tag allowlist. Rendering is
   currently implemented for email only.

2. Write the renderer script the job turn runs. The script writes the body
   (see the requirements below); the body becomes the user-visible message,
   and the session transcript keeps the agent reply.

#### Interactive sessions

In a chat, ask for a reply built by a script (see the requirements below),
for example "build the reply with a script, I need the exact rows". The body
that script writes becomes the user-visible message; a missing or empty body
fails the turn with an error.

For a file-backed message that must arrive without a live chat turn, create a
job bound to the target chat session and edit it in
`<workspace>/cron/jobs.json`: set `"schedule": {"kind": "at", "atMs":
<timestamp in ms>}`, `"deleteAfterRun": true`, and the payload fields from the
step above. The job runs once, delivers the body, and is removed.

#### Renderer script requirements

The renderer script follows the same contract from a cron job and from a chat:

- Read the destination path from the `NANOBOT_BODY_PATH` environment
  variable. Never hardcode the path: it changes from run to run, and the
  variable exists only for commands started by the turn.
- Write a non-empty UTF-8 body of at most 1 MiB: markdown, or HTML when
  `bodyFormat` is `"html"`. The channel sanitizes both formats.
- Exit with status 0 after the body is on disk. A nonzero exit is a tool
  error for the turn.
- Do not send the email or post the message: delivery belongs to the job
  channel. Do not print the body to stdout as a delivery channel either;
  stdout and stderr are diagnostics for the turn.
- A body that is empty, not UTF-8, unreadable, or larger than 1 MiB becomes
  the error text of the reply, and the session transcript keeps the agent
  reply. In a cron job a body that was never written is an error too: the run
  keeps status `ok`, so a broken renderer shows up in the delivered message
  rather than only in the run log.

## Common Patterns

For a nightly report, ask from the target topic:

```text
Every night at 9pm, review today's workspace changes and summarize anything I should handle tomorrow.
```

For a CI follow-up, create a trigger once:

```text
/trigger CI follow-up
```

Then have your CI or webhook adapter call:

```bash
nanobot trigger <trigger-id> "Build failed on main. Inspect the logs and suggest the next fix."
```

For a local report script:

```bash
generate-report | nanobot trigger <trigger-id>
```

## Troubleshooting

If an automation does not run, check that `nanobot gateway` is running, the
automation is enabled, and it was created from a linked topic.

If a local trigger waits forever, confirm the command uses the same workspace or
config as the gateway.

If a trigger message appears twice after a restart, treat it as expected
at-least-once delivery and make the external message idempotent.

If you need to edit, pause, resume, rename, delete, or inspect automations, use
the WebUI Automations view.

## Related Docs

- [`webui.md#automations`](./webui.md#automations) for the browser management view
- [`chat-commands.md#local-triggers`](./chat-commands.md#local-triggers) for `/trigger`
- [`cli-reference.md#local-triggers`](./cli-reference.md#local-triggers) for `nanobot trigger`
- [`configuration.md#gateway-heartbeat`](./configuration.md#gateway-heartbeat) for heartbeat settings
- [`guides/long-running-ai-agent.md`](./guides/long-running-ai-agent.md) for long-running agent work
