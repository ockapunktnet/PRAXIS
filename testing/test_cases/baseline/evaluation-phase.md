```mermaid
flowchart TB
    A[export_event_log]
    B[analyze_event_log]
    C[discover_heuristic_net]
    D[discover_dfg]
    E[render_dfg]
    A --> B
    A --> C
    A --> D
    B --> C
    C --> B
    B --> D
    D --> B
    C --> D
    D --> C
    D --> E
```