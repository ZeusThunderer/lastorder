#!/usr/bin/env node
// Render the Z.ai Coding Plan personal provider for the node harness (T-027).
// Port of dev/zcode-headless-setup/entrypoint.sh render_provider, fresh-file variant:
// the whole ~/.zcode/v2/provider_config.json belongs to this render, and the schema is
// strict (extra keys load silently empty — spike step 0), so the shape is pinned here.
// The key arrives via env, is written 0600 and never printed.
"use strict";
const fs = require("fs");

const file = process.argv[2];
if (!file || !process.env.ZAI_API_KEY) {
  console.error("zcode-provider: usage: ZAI_API_KEY=... node zcode-provider.cjs <file>");
  process.exit(64);
}
const id = process.env.ZCODE_PROVIDER_ID || "zai-cp";
const model = process.env.ZCODE_MODEL || "GLM-5.3-Flash";
const doc = {
  schemaVersion: 1,
  config: {
    providerConfigRules: {
      providerRules: [
        {
          providerId: id,
          templateId: "zai-api", // anthropic-messages -> https://api.z.ai/api/anthropic
          providerName: "Z.ai Coding Plan (node harness)",
          enabled: true,
          config: {
            group: "standard-personal",
            access: { type: "zhipu-coding-plan-api-key", apiKey: process.env.ZAI_API_KEY },
            visibility: "visible",
          },
        },
      ],
    },
    modelConfigRules: { providerModelRules: [], manualProviderModelRules: [] },
    providerOrder: [id],
    defaultModelSelection: { providerId: id, modelId: model },
  },
};
fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n", { mode: 0o600 });
