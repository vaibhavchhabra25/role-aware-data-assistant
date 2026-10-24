// The access token lives in sessionStorage: it survives a reload but not closing the tab.
// (A production deployment would use an httpOnly cookie on the API's own domain.)
const KEY = "data-assistant-token";

export function loadToken(): string | null {
  try {
    return sessionStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function saveToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(KEY, token);
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage unavailable (private mode): the session just won't survive a reload
  }
}
