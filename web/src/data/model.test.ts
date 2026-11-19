import { describe, expect, test } from "vitest";
import type { ModelInfo } from "../wire";
import { environmentWords, modelLabel } from "./model";

const model = (fields: Partial<ModelInfo>): ModelInfo => ({
  id: null,
  name: "get_model()",
  provider: null,
  offline: false,
  expression: "get_model()",
  model_class: null,
  env: null,
  ...fields,
});

describe("how a model is named in a line", () => {
  test("by its name, or by the environment variable it is read from, never the default", () => {
    expect(modelLabel(model({ name: "gpt-5.2", expression: null }))).toBe("gpt-5.2");
    expect(modelLabel(model({}))).toBe("get_model()");
    const fromEnv = model({ env: { variable: "CLINE_MODEL", default: "cline-pass/x" } });
    expect(modelLabel(fromEnv)).toBe("env CLINE_MODEL");
    expect(environmentWords(model({}))).toBeNull();
    expect(environmentWords(fromEnv)).toBe(
      "From environment variable CLINE_MODEL at run time; default cline-pass/x if CLINE_MODEL is not set",
    );
    expect(environmentWords({ ...fromEnv, model_class: "OpenAIChatModel" })).toMatch(
      /^OpenAIChatModel, model name from environment variable CLINE_MODEL/,
    );
  });
});
