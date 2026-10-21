import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { LibraryApp } from '../src/library/LibraryApp';
import { apiErrorFromResponse } from '../src/api-error.js';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
// Only fixed synthetic development tokens are used by the isolated API runner.
(window as any).__sessionToken = 'cycle5-user-a-synthetic';
(window as any).__settledRequests = [];
const apiPage = async (path: string, init: RequestInit = {}) => {
    const token = (window as any).__sessionToken;
    const response = await fetch(`/api/v1${path}`, { ...init, headers: { ...init.headers, Authorization: `Bearer ${token}`, ...(init.body ? { 'Content-Type': 'application/json' } : {}) } });
    const body = await response.json();
    (window as any).__settledRequests.push({ path: path.split('?')[0], token });
    if (!response.ok)
        throw apiErrorFromResponse(response, body);
    return body;
};
const api = async (path: string, init?: RequestInit) => (await apiPage(path, init)).data;
const apiRaw = (path: string, init?: RequestInit) => fetch(`/api/v1${path}`, { ...init, headers: { ...init?.headers, Authorization: `Bearer ${(window as any).__sessionToken}` } });
function Harness() {
    const [epoch, setEpoch] = useState(0);
    return <><header aria-label="Synthetic session controls"><button onClick={() => {
            (window as any).__sessionToken = 'cycle5-user-a-synthetic';
            setEpoch(value => value + 1);
        }}>Use synthetic A</button><button onClick={() => { (window as any).__sessionToken = 'cycle5-user-b-synthetic'; setEpoch(value => value + 1); }}>Use synthetic B</button><output data-testid="auth-epoch">{epoch}</output></header><LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw} authEpoch={epoch}/></>;
}
createRoot(document.getElementById('root')!).render(<Harness />);
