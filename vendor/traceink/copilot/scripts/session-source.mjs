#!/usr/bin/env node

const variable = "TRACEINK_COPILOT_CLOUD_SESSIONS";
const value = process.env[variable]?.trim();

if (value === undefined || value === "" || value === "0") {
  console.log("local");
  process.exit(0);
}

if (value === "1") {
  console.log("cloud");
  process.exit(0);
}

console.error(`${variable} must be 0 or 1; received: ${value}`);
process.exit(2);
