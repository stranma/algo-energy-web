# Edit Permissions Skill

Manage `.claude/settings.json` permission entries.

## File Location

`.claude/settings.json` in the project root.

## Structure

```json
{
  "permissions": {
    "allow": ["Tool(pattern *)"],
    "deny": ["Tool(pattern *)"]
  }
}
```

## Permission Syntax

- `Bash(command *)` -- allow/deny a bash command pattern
- `WebSearch` -- allow web search
- `WebFetch` -- allow web fetch (or `WebFetch(domain:example.com)` for specific domains)
- Use ` *` suffix for wildcard matching (space before asterisk)

## Categories

- **allow**: Auto-approved, no user prompt
- **deny**: Always blocked, cannot be overridden per-session
- **ask** (via omission): Anything not in allow or deny will prompt the user

## Examples

Allow git read commands:
```json
"Bash(git status *)",
"Bash(git log *)",
"Bash(git diff *)"
```

Deny destructive operations:
```json
"Bash(git clean *)",
"Bash(rm -rf *)"
```

## Editing Rules

1. Read the current file first
2. Add entries to the appropriate array
3. Use specific patterns -- avoid overly broad wildcards
4. Test that the pattern matches the intended commands
5. Keep entries sorted by category (git, gh, tools, etc.)
