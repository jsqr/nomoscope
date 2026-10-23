# Contributing

Setup is in the [README](README.md#setup). Run `just check`, `just typecheck`, and `just test`
before pushing.

## Commits

Start the subject with the area or topic: `query: ...`, `docs: ...`. There is
no fixed type vocabulary. The message body is optional and short, just to
explain the motivation.

## Workflow

Work in a branch. Pushing a branch runs CI. Squash-merge to main, either
locally (`git merge --squash`) or on GitHub. The person merging should write
the squash message. Pull requests are optional for committers.

## AI policy

- AI tools are allowed.
- A human should take responsibility for every contribution. AI is never listed
  as an author or contributor: no `Co-authored-by` or `Generated-by` trailers,
  no bot commits.
- Humans should direct the work, including structural design, data structures, and
  algorithms.
- Humans write all commit messages on main, pull request descriptions, issues.
- Disclose AI use with a trailer: `Tool: <tool> (<model>)`.

References: the Linux kernel's
[AI Coding Assistants](https://docs.kernel.org/process/coding-assistants.html)
and Fedora's
[AI-Assisted Contributions Policy](https://docs.fedoraproject.org/en-US/council/policy/ai-contribution-policy/).
Both name the trailer `Assisted-by:`; that's okay, but `Tool:` is preferred
to avoid anthropomorphizing.
