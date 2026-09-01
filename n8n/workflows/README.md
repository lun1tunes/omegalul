# n8n workflows

- `core/` — production MAS runtime. **Import only via n8n UI** (Import from File), порядок — `runtime_import_order` в [`../import-manifest.json`](../import-manifest.json). Канон: [`../../docs.md`](../../docs.md) §2.
- `support/` — не на live user path: specialist template, Cluster/Binary/Presentation stubs, AI-components reference, standalone Excel form. Field guide: [`../../docs.md`](../../docs.md) §6.
- `retired/` — Engineering MAS (CAS, Data Tables, Entry Form, Human Gate, Trace Writer, Activity Hydrate, старый SCHEDULE Builder). Не импортировать.
