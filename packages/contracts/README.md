# Doco Backend Contracts

This package owns the versioned protobuf source and generated Python bindings used by Chat and Document services.

Regenerate bindings from the backend root:

```bash
./scripts/generate-document-contract.sh
```

The Python import namespace is `backend_contracts.documents.v1`. The protobuf wire package remains `documents.v1`.
