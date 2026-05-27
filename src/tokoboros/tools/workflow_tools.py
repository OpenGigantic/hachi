"""
Workflow Management Tools

Provides tools for the agent to manage long-running, multi-step workflows.
This includes the ability to suspend execution to wait for human approval
or asynchronous events (webhook callbacks, cron schedules).
"""

from tokoboros.core.loop import ToolRegistry, SuspendWorkflow

def register_workflow_tools(registry: ToolRegistry):
    """Mount workflow orchestration tools into the LLM's active toolset."""

    def suspend_workflow(reason: str) -> str:
        """
        Signals the ExecutionLoop to suspend immediately.
        The exception is caught by the loop, which safely checkpoints state
        and halts execution.
        """
        raise SuspendWorkflow(reason=reason)

    registry.register({
        "name": "core_suspend_workflow",
        "description": (
            "Suspend the current workflow and wait for an external event. "
            "Use this when your instructions say 'Wait for human approval', "
            "'Wait for a webhook', or 'Pause execution until...'. "
            "Your state will be safely saved and you will automatically wake up "
            "when the event occurs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "The exact reason you are suspending (e.g., 'Waiting for CEO approval in #devops')."
                }
            },
            "required": ["reason"]
        }
    }, suspend_workflow)
