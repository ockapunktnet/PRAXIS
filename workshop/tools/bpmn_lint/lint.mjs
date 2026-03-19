/**
 * BPMN Lint wrapper — validates BPMN XML using bpmnlint (bpmn.io).
 *
 * Usage:  node lint.mjs <path-to-bpmn-file>
 * Output: JSON on stdout
 */

import { readFileSync } from "fs";
import { createRequire } from "module";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const require = createRequire(join(__dirname, "__placeholder__.js"));

const BpmnModdle = require("bpmn-moddle");
const { Linter } = require("bpmnlint");
const NodeResolver = require("bpmnlint/lib/resolver/node-resolver");

const SEVERITY_MAP = { off: "off", warn: "warning", error: "error", info: "info", "rule-error": "error" };

async function main() {
  const filePath = process.argv[2];
  if (!filePath) {
    console.error("Usage: node lint.mjs <bpmn-file>");
    process.exit(1);
  }

  const xml = readFileSync(filePath, "utf-8");

  // Parse BPMN XML
  const moddle = new BpmnModdle();
  let parseResult;
  try {
    parseResult = await moddle.fromXML(xml);
  } catch (err) {
    const result = {
      valid: false,
      issues: [
        {
          rule: "xml-parse-error",
          id: null,
          message: `Failed to parse BPMN XML: ${err.message}`,
          severity: "error",
        },
      ],
    };
    process.stdout.write(JSON.stringify(result));
    process.exit(0);
  }

  // Lint using NodeResolver (resolves built-in rules & recommended config)
  const resolver = new NodeResolver();
  const linter = new Linter({ resolver });
  const config = { extends: "bpmnlint:recommended" };

  let lintResults;
  try {
    lintResults = await linter.lint(parseResult.rootElement, config);
  } catch (err) {
    const result = {
      valid: false,
      issues: [
        {
          rule: "lint-error",
          id: null,
          message: `Linting failed: ${err.message}`,
          severity: "error",
        },
      ],
    };
    process.stdout.write(JSON.stringify(result));
    process.exit(0);
  }

  // Flatten: lintResults is { ruleName: [ { id, message, category, ... }, ... ] }
  const issues = [];
  for (const [ruleName, reports] of Object.entries(lintResults)) {
    for (const report of reports) {
      issues.push({
        rule: ruleName,
        id: report.id || null,
        message: report.message,
        severity: SEVERITY_MAP[report.category] || "warning",
      });
    }
  }

  const result = {
    valid: issues.filter((i) => i.severity === "error").length === 0,
    issues,
  };

  process.stdout.write(JSON.stringify(result));
}

main().catch((err) => {
  console.error(err);
  process.exit(2);
});
