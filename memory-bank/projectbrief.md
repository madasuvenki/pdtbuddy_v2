# Project Brief: PDTBuddy (Buddy_DB_Ext_13_10)

## Project Name
**PDTBuddy** — PDT Statistics Dashboard & Reporting Platform

## Version
`v2.7`

## Repository
- **Primary**: `origin` → `https://github.com/madasuvenki/Buddy_notjson.git`
- **Secondary**: `pdtbuddy_v2` → `https://github.com/madasuvenki/pdtbuddy_v2.git`

## Core Purpose
PDTBuddy is a Qualcomm-internal web application that serves as a centralized statistics dashboard and reporting platform for PDT (Product Development Testing) teams. It aggregates data from multiple internal systems (Orbit, Jira, Axiom, MySQL) and presents it through a rich web UI with role-based access control.

## Primary Goals
1. Provide real-time and historical MTBF (Mean Time Between Failures) tracking per target/BU
2. Centralize CR (Change Request) management, analysis, and comparison across business units
3. Enable live status publishing and viewing for test campaigns
4. Generate weekly summary reports and device summaries
5. Support automotive-specific (Gen5, Gen45) and WBC (Wireless Business Connectivity) reporting
6. Provide AI-assisted CR summaries via QGenie/ChatWise integration
7. Offer a text-to-SQL chatbot for ad-hoc data queries

## Scope
- Multi-BU (Business Unit) support with per-BU MySQL database schemas
- Multi-region support: QIPL (Hyderabad/India), SD (San Diego/USA), CH (China)
- Role-based access: admin users, bypass users, LDAP group-based authorization
- Public API endpoints for external consumers (no login required)
- Optional MCP (Model Context Protocol) server for MTBF data exposure
- PyInstaller packaging support for standalone `.exe` distribution

## Key Constraints
- Qualcomm LDAP authentication required (qed-ldap.qualcomm.com)
- MySQL database backend (pdt_stats_dashboard schema + per-BU schemas)
- Internal Qualcomm network access required for Orbit, Jira, QGenie integrations
- Session idle timeout: 2 hours (30 days with "Keep me signed in")