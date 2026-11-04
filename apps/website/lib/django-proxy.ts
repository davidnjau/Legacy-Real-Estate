import { NextResponse } from 'next/server';

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000/api/v1';

type ProxyOptions = {
  method?: string;
  body?: unknown;
};

function mergeCookies(...cookieStrings: string[]) {
  const cookies = new Map<string, string>();
  for (const cookieString of cookieStrings) {
    for (const pair of cookieString.split(';')) {
      const separator = pair.indexOf('=');
      if (separator <= 0) continue;
      cookies.set(pair.slice(0, separator).trim(), pair.slice(separator + 1).trim());
    }
  }
  return [...cookies].map(([name, value]) => `${name}=${value}`).join('; ');
}

function cookiePairs(setCookies: string[]) {
  return setCookies.map((cookie) => cookie.split(';', 1)[0]).filter(Boolean);
}

export async function forwardDjangoRequest(
  request: Request,
  path: string,
  options: ProxyOptions = {},
) {
  const method = options.method || request.method;
  const incomingCookie = request.headers.get('cookie') || '';
  let csrfResponse: Response | null = null;
  let csrfToken: string | null = null;

  if (!['GET', 'HEAD', 'OPTIONS'].includes(method.toUpperCase())) {
    csrfResponse = await fetch(`${apiBaseUrl}/auth/csrf`, {
      headers: { Cookie: incomingCookie },
      cache: 'no-store',
    });
    if (!csrfResponse.ok) {
      throw new Error(`Django CSRF endpoint returned ${csrfResponse.status}.`);
    }
    const csrfPayload = await csrfResponse.json();
    if (typeof csrfPayload.csrfToken !== 'string' || !csrfPayload.csrfToken) {
      throw new Error('Django CSRF endpoint returned an invalid token.');
    }
    csrfToken = csrfPayload.csrfToken;
  }

  const csrfSetCookies = csrfResponse?.headers.getSetCookie() || [];
  const headers = new Headers({ Cookie: mergeCookies(incomingCookie, ...cookiePairs(csrfSetCookies)) });
  if (options.body !== undefined) {
    headers.set('Content-Type', 'application/json');
  }
  if (csrfToken) headers.set('X-CSRFToken', csrfToken);

  const response = await fetch(`${apiBaseUrl}${path}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: 'no-store',
  });
  const contentType = response.headers.get('content-type') || 'application/json';
  let responseBody = await response.text();
  let responseContentType = contentType;
  if (contentType.includes('application/problem+json')) {
    try {
      const problem = JSON.parse(responseBody);
      responseBody = JSON.stringify({ error: problem.detail || problem.title || 'The request failed.' });
      responseContentType = 'application/json';
    } catch (error) {
      throw new Error(`Django returned an invalid problem response: ${String(error)}`);
    }
  }

  const result = new NextResponse(responseBody, {
    status: response.status,
    headers: { 'Content-Type': responseContentType },
  });
  for (const cookie of response.headers.getSetCookie()) {
    result.headers.append('Set-Cookie', cookie);
  }
  for (const cookie of csrfSetCookies) {
    result.headers.append('Set-Cookie', cookie);
  }
  return result;
}

export function djangoProxyFailure(error: unknown, fallback: string) {
  console.error(fallback, error);
  return NextResponse.json({ error: fallback }, { status: 502 });
}
