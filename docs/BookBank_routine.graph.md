# Execution graph — BookBank_routine.aspec.py

## BookbankRun

```mermaid
flowchart TD
    _inputs(["inputs: freeform_context"])
    workspace["workspace = ResolveWorkspace"]
    plugin["plugin = VerifyPlugin"]
    issue["issue = SelectIssue"]
    mark["mark = MarkStarted"]
    build["build = GenerateBook"]
    art["art = OpenImageRequests"]
    notify["notify = NotifyIssue"]
    alert["alert = PushAlert"]
    outcomes{{"outcomes = declared endings"}}
    build --> outcomes
    issue --> outcomes
    mark --> outcomes
    plugin --> outcomes
    workspace --> outcomes
    workspace -. "workspace.resolved" .-> plugin
    _inputs --> issue
    plugin -. "plugin.usable" .-> issue
    workspace --> issue
    issue -. "issue.proceed" .-> mark
    issue --> build
    mark -. "mark.marked" .-> build
    plugin --> build
    build -. "build.built" .-> art
    build -. "build.built" .-> notify
    issue --> notify
    art --> alert
    build --> alert
    issue --> alert
    notify --> alert
    outcomes --> alert
    plugin --> alert
    workspace --> alert
```
