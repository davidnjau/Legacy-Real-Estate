import createClient from "openapi-fetch";

import type { paths } from "./generated/schema.js";

export type ApiClientOptions = {
  baseUrl: string;
  token?: string;
  fetch?: typeof globalThis.fetch;
};

export function createApiClient({ baseUrl, token, fetch }: ApiClientOptions) {
  return createClient<paths>({
    baseUrl,
    ...(fetch ? { fetch } : {}),
    ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
  });
}

export type { components, operations, paths } from "./generated/schema.js";
