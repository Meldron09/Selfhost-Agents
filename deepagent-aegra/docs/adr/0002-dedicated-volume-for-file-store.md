# Dedicated Docker volume for the file store, not the bind-mounted project source tree

Uploaded Attachment and Output file bytes (see CONTEXT.md's "Key") live on local disk under a dedicated named Docker volume declared in `deepagent-aegra`'s `docker-compose.yml` (mirroring how `aegra-host-pg-data` is already separate from the project source mount), mounted at its own container path (e.g. `/app/data`) — not under the path the project source tree is bind-mounted to.

The reference `aegra-host/docker-compose.yml` bind-mounts the host's actual working tree into the container for live-reload dev: `../${PROJECT_SRC_DIR:-.}:/app/project`. Reusing that same path as the file store's root was the zero-new-config alternative, and was rejected: it would make every uploaded or generated file a write into the host's live git working tree, indistinguishable from a source-code change and subject to accidental commits or `git status` noise. A dedicated volume keeps runtime data (accumulates per-run, empty on a fresh checkout) cleanly separate from source (checked into git, what `PROJECT_SRC_DIR` is for).

This is a real commitment, not free to reverse later: the upload/download HTTP app's own storage code will be built against whatever mount path is settled here, and migrating means moving on-disk bytes plus updating every reference to the old path, not just editing a compose file.

Resolved while working wayfinder ticket [Decide: adapt aegra-host scaffold for deepagent-aegra (compose services, env vars, Ollama connectivity)](https://github.com/Meldron09/Selfhost-Agents/issues/6).
