/**
 * A JSON Schema as a field table: what a tool takes, what an agent returns. The reading of
 * the schema (`schemaRows`) is pure and separate from the table so it can be tested alone.
 */
import type { Json } from "../wire";
import { cx } from "./tone";

type Schema = { [k: string]: Json };

export type SchemaRow = {
  /** Dotted path: "address.city", "items[].id". */
  path: string;
  depth: number;
  type: string;
  required: boolean;
  description: string | null;
  /** Short facts: "default 3", "min 1", "format date". */
  notes: string[];
};

const MAX_DEPTH = 4;

const isSchema = (value: Json | undefined): value is Schema =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function resolve(schema: Schema, root: Schema, hops = 0): Schema {
  const ref = schema.$ref;
  if (typeof ref !== "string" || hops > 8) return schema;
  const match = /^#\/(\$defs|definitions)\/(.+)$/.exec(ref);
  const defs = match?.[1] ? root[match[1]] : undefined;
  const target = match?.[2] && isSchema(defs) ? defs[match[2]] : undefined;
  return isSchema(target) ? resolve(target, root, hops + 1) : schema;
}

function refName(schema: Schema): string | null {
  return typeof schema.$ref === "string" ? (schema.$ref.split("/").at(-1) ?? null) : null;
}

/**
 * The source text of a type that static analysis could not follow to a schema, which it
 * writes as `{"x-python-type": "Verdict[Claim]"}`. Null for every other schema.
 */
export function unresolvedType(schema: Json | null | undefined): string | null {
  const text = isSchema(schema) ? schema["x-python-type"] : undefined;
  return typeof text === "string" ? text : null;
}

/** A compact type expression: "string", "integer[]", "Order | null", '"a" | "b"'. */
export function schemaType(schema: Json | undefined, root: Schema): string {
  if (schema === true || schema === undefined) return "any";
  if (!isSchema(schema)) return "any";
  const unresolved = unresolvedType(schema);
  if (unresolved !== null) return unresolved;
  const named = refName(schema);
  if (named) return named;
  for (const key of ["anyOf", "oneOf"] as const) {
    const options = schema[key];
    if (Array.isArray(options)) {
      return [...new Set(options.map((o) => schemaType(o, root)))].join(" | ") || "any";
    }
  }
  if (Array.isArray(schema.enum)) return schema.enum.map((v) => JSON.stringify(v)).join(" | ");
  if ("const" in schema) return JSON.stringify(schema.const);
  const type = schema.type;
  if (Array.isArray(type)) return type.map(String).join(" | ");
  if (type === "array") return `${wrapUnion(schemaType(schema.items, root))}[]`;
  if (type === "object" || isSchema(schema.properties)) {
    return typeof schema.title === "string" ? schema.title : "object";
  }
  return typeof type === "string" ? type : "any";
}

const wrapUnion = (type: string): string => (type.includes(" | ") ? `(${type})` : type);

function notesOf(schema: Schema): string[] {
  const notes: string[] = [];
  if ("default" in schema) notes.push(`default ${JSON.stringify(schema.default)}`);
  const facts: [string, string][] = [
    ["format", "format"],
    ["minimum", "min"],
    ["exclusiveMinimum", "more than"],
    ["maximum", "max"],
    ["exclusiveMaximum", "less than"],
    ["minLength", "min length"],
    ["maxLength", "max length"],
    ["minItems", "min items"],
    ["maxItems", "max items"],
    ["pattern", "pattern"],
  ];
  for (const [key, word] of facts) {
    const value = schema[key];
    if (typeof value === "string" || typeof value === "number") notes.push(`${word} ${value}`);
  }
  return notes;
}

/** The object schema whose fields should be listed under this one, if any. */
function nested(schema: Schema, root: Schema): { schema: Schema; suffix: string } | null {
  const resolved = resolve(schema, root);
  if (isSchema(resolved.properties)) return { schema: resolved, suffix: "" };
  if (resolved.type === "array" && isSchema(resolved.items)) {
    const items = resolve(resolved.items, root);
    if (isSchema(items.properties)) return { schema: items, suffix: "[]" };
  }
  for (const key of ["anyOf", "oneOf"] as const) {
    const options = resolved[key];
    if (!Array.isArray(options)) continue;
    // "X | null" is the common optional-object shape: list X's fields.
    const real = options.filter((o) => isSchema(o) && o.type !== "null");
    if (real.length === 1 && isSchema(real[0])) return nested(real[0], root);
  }
  return null;
}

/**
 * Flattens an object schema into rows. Returns null when the schema has no named fields
 * (a bare string, a list, an empty or unknown schema): show `schemaType` instead.
 */
export function schemaRows(schema: Json | null | undefined): SchemaRow[] | null {
  if (!isSchema(schema)) return null;
  const root = schema;
  const top = nested(schema, root);
  if (top?.suffix !== "") return null;

  const rows: SchemaRow[] = [];
  const walk = (object: Schema, prefix: string, depth: number, seen: Schema[]): void => {
    const properties = object.properties;
    if (!isSchema(properties)) return;
    const required = new Set(Array.isArray(object.required) ? object.required : []);
    for (const [name, raw] of Object.entries(properties)) {
      const field = isSchema(raw) ? raw : {};
      const resolved = resolve(field, root);
      const description = field.description ?? resolved.description;
      rows.push({
        path: `${prefix}${name}`,
        depth,
        type: schemaType(field, root),
        required: required.has(name),
        description: typeof description === "string" ? description : null,
        notes: notesOf(resolved),
      });
      const child = nested(field, root);
      // Recursive models (a tree node holding tree nodes) are listed once.
      if (child && depth + 1 < MAX_DEPTH && !seen.includes(child.schema)) {
        walk(child.schema, `${prefix}${name}${child.suffix}.`, depth + 1, [...seen, child.schema]);
      }
    }
  };
  walk(top.schema, "", 0, [top.schema]);
  return rows;
}

export function SchemaTable({
  schema,
  caption,
  className,
}: {
  schema: Json | null | undefined;
  /** Names the table for assistive tech: "Parameters of get_order". */
  caption: string;
  className?: string;
}) {
  const rows = schemaRows(schema);
  if (rows === null) {
    const type = isSchema(schema) ? schemaType(schema, schema) : "any";
    return (
      <p className={cx("m-0 text-muted text-ui", className)}>
        {caption}: <code className="text-fg">{type}</code>
        {unresolvedType(schema) !== null &&
          ". The type is shown as the source names it: what it holds is decided at run time."}
      </p>
    );
  }
  if (rows.length === 0) {
    return <p className={cx("m-0 text-muted text-ui", className)}>{caption}: no fields.</p>;
  }
  const th = "border-line border-b px-2 py-1.5 text-left font-medium text-faint text-meta";
  return (
    <div className={cx("overflow-x-auto rounded-control border border-line", className)}>
      <table className="w-full border-collapse text-data">
        <caption className="sr-only">{caption}</caption>
        <thead className="bg-surface-2">
          <tr>
            <th scope="col" className={th}>
              Field
            </th>
            <th scope="col" className={th}>
              Type
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.path} className="border-line border-b align-top last:border-b-0">
              {/* The description sits under the name: a third column leaves it a few words
                  per line in a narrow inspector. */}
              <th scope="row" className="px-2 py-1.5 text-left font-normal">
                {/* break-all: a long snake_case name has no break opportunity of its own, and
                    unbroken it pushes the Type column out of a narrow inspector. */}
                <span
                  className="break-all font-mono text-fg"
                  style={{ paddingLeft: row.depth * 12 }}
                  title={row.path}
                >
                  {row.depth > 0 ? row.path.split(".").at(-1) : row.path}
                </span>
                <span
                  className={cx("ml-1.5 text-meta", row.required ? "text-muted" : "text-faint")}
                >
                  {row.required ? "required" : "optional"}
                </span>
                {row.description && (
                  <span className="mt-0.5 block text-muted" style={{ paddingLeft: row.depth * 12 }}>
                    {row.description}
                  </span>
                )}
                {row.notes.length > 0 && (
                  <span
                    className="mt-0.5 block font-mono text-faint text-meta"
                    style={{ paddingLeft: row.depth * 12 }}
                  >
                    {row.notes.join(" · ")}
                  </span>
                )}
              </th>
              <td className="w-[42%] break-words px-2 py-1.5 font-mono text-tool">{row.type}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
