# leader-plan

Act as the Technical Lead and Project Manager.

Produce a clear implementation plan with the following sections:
1. Scope (what is in / out)
2. File ownership (folders touched)
3. Contracts impacted (APIs, schemas, shared types)
4. Acceptance criteria (testable conditions)
5. Dependencies (what must be completed or merged first)

For every unit of planned work, you MUST output an "Agent Launch Instructions" section
using the following exact format:

Agent Launch Instructions:

1. Agent: <agent-command>
   Ticket: <ticket-id and short name>
   Branch: <branch-name>
   Prompt:
   "<exact, copy-paste-ready prompt for the agent>"

Rules:
- One ticket = one agent = one branch
- Do not assign work that violates ownership boundaries
- Do not assign work whose dependencies are unmet
- Branch names must be explicit and unique
- Prompts must reference authoritative docs (e.g., docs/MSDD.md, contracts/*)

Do not write code.
Ask clarifying questions first if requirements are unclear.
Ensure the plan and agent prompts can be executed without further interpretation.
