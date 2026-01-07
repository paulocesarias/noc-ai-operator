# NOC AI Operator

AI-driven NOC operator replacement for automated infrastructure monitoring and remediation.

## Overview

NOC AI Operator monitors your infrastructure, interprets alerts using AI (Claude), and executes remediation actions automatically. It supports both modern cloud-native systems and legacy on-premises infrastructure.

## Features

- **Real-time Alert Ingestion** - AlertManager webhooks, syslog, SNMP traps
- **AI-Powered Analysis** - Claude API for intelligent alert interpretation
- **Automated Remediation** - Kubernetes actions, Ansible playbooks, SSH/CLI commands
- **Legacy System Support** - SNMP, SSH/CLI adapters for on-premises infrastructure
- **Runbook Knowledge Base** - RAG system for contextual decision making
- **Approval Workflows** - Human-in-the-loop for risky operations

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                    AI Decision Engine                   │
│            (Claude + Runbook RAG + History)            │
└────────────────────┬───────────────────────────────────┘
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
┌─────────────────┐     ┌─────────────────┐
│  Modern Stack   │     │  Legacy Adapter │
│  (K8s API,      │     │  Service        │
│   Prometheus)   │     │  (Python)       │
└─────────────────┘     └────────┬────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
        ┌──────────┐      ┌──────────┐      ┌──────────┐
        │ SNMP     │      │ SSH/CLI  │      │ Syslog   │
        │ Devices  │      │ Storage  │      │ Sources  │
        └──────────┘      └──────────┘      └──────────┘
```

## Project Structure

```
noc-ai-operator/
├── src/
│   ├── core/              # Core event processing and orchestration
│   ├── adapters/          # Data collection adapters
│   │   ├── snmp/          # SNMP trap receiver and poller
│   │   ├── ssh/           # SSH/CLI automation
│   │   ├── syslog/        # Syslog receiver
│   │   └── prometheus/    # Prometheus/AlertManager integration
│   ├── actions/           # Remediation action executors
│   │   ├── kubernetes/    # K8s remediation (restart, scale, rollback)
│   │   └── ansible/       # Ansible playbook runner
│   ├── ai/                # AI/ML components
│   │   ├── llm/           # Claude API integration
│   │   └── rag/           # Runbook knowledge base
│   ├── api/               # REST API endpoints
│   └── dashboard/         # Web dashboard
├── tests/                 # Unit and integration tests
├── config/                # Configuration files
├── scripts/               # Utility scripts
├── docs/                  # Documentation
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Quick Start

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- Kubernetes cluster (for K8s actions)
- Claude API key

### Installation

```bash
# Clone the repository
git clone https://github.com/paulocesarias/noc-ai-operator.git
cd noc-ai-operator

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Copy and configure environment
cp config/config.example.yaml config/config.yaml
# Edit config/config.yaml with your settings
```

### Running

```bash
# Development mode
python -m src.main

# With Docker
docker-compose up -d

# Deploy to Kubernetes
kubectl apply -k k8s/overlays/prod/
```

## Configuration

See `config/config.example.yaml` for all configuration options.

Key environment variables:
- `ANTHROPIC_API_KEY` - Claude API key
- `ALERTMANAGER_URL` - AlertManager webhook endpoint
- `KUBECONFIG` - Path to kubeconfig for K8s actions
- `DATABASE_URL` - PostgreSQL connection string

## Documentation

- [Design Document](https://paulocesarias.atlassian.net/wiki/spaces/SD/pages/7536642/NOC+AI+Operator+-+Design+Document)
- [Jira Project](https://paulocesarias.atlassian.net/jira/software/projects/NOC/board)

## License

MIT
