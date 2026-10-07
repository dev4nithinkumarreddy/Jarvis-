# Agent Rules

These rules govern the development of Jarvis across all phases. Every phase must adhere to these rules without exception.

1. **Verify All Dependencies and Signatures**: Never invent package names, function signatures or CLI flags. Verify with `pip index versions <pkg>`, `pip show`, `help()` or the package's official docs. If you cannot verify something, write it under "Unverified assumptions" in your final summary.
2. **Safe Process Execution**: Never use `shell=True`. Never execute model-generated strings as code.
3. **Immutable Risk Tiers**: Risk tiers are defined in code on each tool. The LLM can never change them.
4. **Strict Path Guarding**: All file access goes through the path guard (resolved absolute paths, must be inside allowed roots from config.yaml, symlinks resolved before checking).
5. **Mandatory Audit Logging**: Every tool call is written to the audit log before and after execution.
6. **Zero Disk Secret Storage**: Secrets (API keys) come from environment variables only. Never write them to disk, logs or config.
7. **Strict Phase Scoping**: Keep each phase within its stated scope. If something outside scope seems necessary, stop and ask.
8. **Phase Completion Standards**: Every phase ends with: a summary of what was built, how to run it, how to test it, unverified assumptions, and known limitations.
