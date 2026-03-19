```mermaid
flowchart TB
    F[remodel_bpmn]
    G[render_bpmn]
    H[lint_bpmn]
    I[convert_bpmn_to_petri_net]
    J[render_petri_net]
    K[check_petri_net_property]
    F --> G
    F --> H
    F --> I
    G --> H
    H --> G
    G --> I
    I --> G
    H --> I
    I --> H
    I --> J
    I --> K
    J --> K
    K --> J
```