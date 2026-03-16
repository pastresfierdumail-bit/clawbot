"""
core/tools.py — Définition des tools pour le function calling Kimi K2.

Chaque tool est décrit au format OpenAI function calling.
L'agent décide quand et comment les appeler.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Execute a shell command (PowerShell) on the VM. Use for installing packages, running scripts, system operations. DANGEROUS commands will be blocked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (PowerShell syntax)"
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Working directory for the command. Default: C:\\Openclaw"
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds. Default: 30, max: 300"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read the contents of a file. Use to inspect code, configs, logs, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file"
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Max lines to read (default: 200, for large files)"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file (creates or overwrites). Use for creating scripts, configs, code files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file"
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "file_list",
            "description": "List files and directories in a given path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list"
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "List recursively (default: false, max depth 3)"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": "Take a screenshot of the VM screen and analyze it with Gemini Vision. Use to see what's happening on screen (Blender, browser, etc).",
            "parameters": {
                "type": "object",
                "properties": {
                    "analyze": {
                        "type": "boolean",
                        "description": "If true, sends screenshot to Gemini Vision for analysis (default: true)"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Custom prompt for vision analysis (default: 'Describe what you see on screen')"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "app_launch",
            "description": "Launch an application on the VM. Supported: blender, vscode, chrome, unreal, n8n, notion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {
                        "type": "string",
                        "enum": ["blender", "vscode", "chrome", "unreal", "n8n", "notion"],
                        "description": "Application to launch"
                    },
                    "args": {
                        "type": "string",
                        "description": "Arguments (e.g., URL for chrome, project path for vscode)"
                    }
                },
                "required": ["app_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "git_command",
            "description": "Run a git command in a repository. Use for version control operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Git subcommand and args (e.g., 'status', 'add .', 'commit -m msg', 'push')"
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Path to the git repository"
                    }
                },
                "required": ["command", "repo_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for information. Returns summarized results. Use for research, documentation lookup, job searching, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (default: 5, max: 10)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "Save information to persistent memory. Use to remember facts, project context, research results, user preferences.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["projects", "research", "tasks", "preferences", "notes"],
                        "description": "Memory category"
                    },
                    "key": {
                        "type": "string",
                        "description": "Unique key for this memory entry"
                    },
                    "content": {
                        "type": "string",
                        "description": "The information to remember"
                    }
                },
                "required": ["category", "key", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": "Recall information from persistent memory. Use to retrieve previously saved facts, context, research.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["projects", "research", "tasks", "preferences", "notes", "all"],
                        "description": "Memory category to search (or 'all')"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query to filter results (optional, returns all if empty)"
                    }
                },
                "required": ["category"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "report_save",
            "description": "Save a structured report (daily activity, research results, etc). These reports are retrievable via /report command.",
            "parameters": {
                "type": "object",
                "properties": {
                    "report_type": {
                        "type": "string",
                        "enum": ["daily", "research", "task_complete", "error"],
                        "description": "Type of report"
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title for the report"
                    },
                    "content": {
                        "type": "string",
                        "description": "Full report content (markdown)"
                    }
                },
                "required": ["report_type", "title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_task",
            "description": "Schedule a task to be executed later by the agent. Useful for recurring checks, long-running processes, or follow-ups.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "The task description for the agent to execute"
                    },
                    "schedule": {
                        "type": "string",
                        "enum": ["daily", "hourly", "once"],
                        "description": "Frequency"
                    },
                    "time": {
                        "type": "string",
                        "description": "Time in HH:MM format (for daily schedule)"
                    }
                },
                "required": ["description", "schedule"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List all currently scheduled autonomous tasks.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kb_update",
            "description": "Add or update knowledge in the Hierarchical Knowledge Base. Organizes info by global task and sub-theme for long-term consistency.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {
                        "type": "string",
                        "description": "Global task name (e.g., 'Blender Navigator')"
                    },
                    "theme": {
                        "type": "string",
                        "description": "Sub-theme or component (e.g., 'Vision Logic')"
                    },
                    "content": {
                        "type": "string",
                        "description": "Fact, insight, or status update to remember"
                    }
                },
                "required": ["task_name", "theme", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "kb_query",
            "description": "Query the Knowledge Base for context on a specific task or theme.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_name": {
                        "type": "string",
                        "description": "Filter by global task (optional)"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search term to find relevant knowledge"
                    }
                },
                "required": ["query"]
            }
        }
    },
]

# Noms des tools pour lookup rapide
TOOL_NAMES = [t["function"]["name"] for t in TOOLS]
