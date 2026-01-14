---
name: ownership-boundaries
description: Ownership boundaries (prevents FE/BE/Quant stepping on each other)
---

# Overview

Folder ownership:

- Frontend owns: frontend/** (or apps/web/**)
- Backend owns: backend/** (or apps/api/**)
- Quant/Analytics owns: research/**
- Shared types/contracts only in: shared/** or contracts/**

Do not modify out-of-scope folders unless the task explicitly requires it.