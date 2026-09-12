# Changelog

All notable changes to this project are documented in this file.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/); this
project doesn't use version numbers, so entries are dated instead.

## 2026-09-11

### Fixed

- Production app (Render) threw an error on the first chat message sent in
  a session. Render logs showed
  `agent_framework.exceptions.ChatClientException` wrapping a `404
  DeploymentNotFound` from Azure OpenAI: the `AZURE_OPENAI_CHAT_DEPLOYMENT_NAME`
  environment variable configured in Render's dashboard didn't match an
  actual deployment on the Azure AI Foundry resource. Corrected in Render's
  dashboard; no code change was needed.

### Changed

- `requirements.txt` now pins exact dependency versions (`==`) instead of
  open-ended lower bounds (`>=`), matching the versions already confirmed
  working in local development. Prevents a future container rebuild from
  silently resolving a different, untested version of a dependency (e.g.
  `agent-framework-openai`, `gradio`) and changing runtime behavior between
  local and deployed environments.
