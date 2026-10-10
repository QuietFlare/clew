# The agent: a person keeps the decisions

The agent that runs steps 3 and 4 for you is a
[Mainsheet](https://github.com/QuietFlare/mainsheet) agent. Mainsheet is
our agent runtime: it reads one definition file, runs the model with
exactly the tools that file allows, checks every tool call against a
policy before it runs, keeps it inside a sandbox with no network, and
signs a record of the run. `clew ui` and `clew build` start it for you,
so both need Mainsheet installed in the same environment:

```bash
pip install "clew-lineage[agent]"
clew ui
```

`clew ui` is a page on this machine: pick a run folder, write the
incident, and watch triage, impact and evidence appear as their files
do. A held incident shows a decision card, and the agent has no tool to
decide one. The definition it runs is
[clew/agent/agent.yaml](clew/agent/agent.yaml): the model, the prompt,
the seven Clew tools, and the limits on each.

```bash
clew serve --dir /path/to/dir
```

`clew serve` needs no Mainsheet. It offers the same seven steps as tools
over MCP to any agent that speaks it, Claude Code, Cursor or your own:
inbox, triage, impact, seal, options, the incident text, and a
recommendation on a held incident. No tool takes a trigger, which
travels from triage to impact in the record on disk, and no tool decides
a held incident. [skills/clew-incident](skills/clew-incident/SKILL.md)
gives the flow to a person's own coding agent. What such an agent lacks
is Mainsheet's gate and record, which is why the tools are built to be
safe by what they take.
