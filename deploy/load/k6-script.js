// Нагрузочный сценарий «Орбита ФСП» — заготовка (прогон запланирован на 10 октября,
// таймбокс 1 час; цели — PLAN.md §7.5, результат — docs/validation-report.md §H).
//
// Два сценария:
//   search — работодатель: поиск кандидатов, карточка, версия подборки (30–50 VU, 5 минут);
//            цели p95: подборка ≤ 500 мс, карточка ≤ 200 мс, поиск ≤ 300 мс.
//   runner — кандидат проходит демо-тест при LLM_PROVIDER=stub (20 VU, без расхода токенов);
//            цель: приём ответа p95 ≤ 300 мс. Отдельно 5 VU с реальным LLM — вручную.
//
// Аутентификация: боевые клиенты Keycloak без Direct Access Grants, поэтому токены приходят
// через env (EMPLOYER_TOKEN, CANDIDATE_TOKEN) — получить в браузере после входа (DevTools →
// Application → Keycloak token) либо через выделенный loadtest-клиент, если решим его добавить.
//
// Запуск: make load-test (k6 локально) либо docker run grafana/k6 … (deploy/load/README.md);
// SMOKE=1 — короткий санити-прогон (5 VU / 30 с).
//
// TODO (после реализации API): need_id для подборки — из POST /needs в setup(); ответы раннера —
// по kind задания из GET /tests/sessions/{id}/current.

import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE_URL || "http://localhost:8000";
const EMPLOYER_TOKEN = __ENV.EMPLOYER_TOKEN || "";
const CANDIDATE_TOKEN = __ENV.CANDIDATE_TOKEN || "";
const SPEC = __ENV.SPECIALIZATION || "be";
const GRADE = __ENV.TARGET_GRADE || "middle";
const SMOKE = (__ENV.SMOKE || "") === "1";

export const options = SMOKE
  ? {
      scenarios: {
        smoke: { executor: "constant-vus", vus: 5, duration: "30s", exec: "search" },
      },
      thresholds: { http_req_failed: ["rate<0.01"], checks: ["rate>0.99"] },
    }
  : {
      scenarios: {
        search: {
          executor: "ramping-vus",
          exec: "search",
          startVUs: 0,
          stages: [
            { duration: "1m", target: 30 },
            { duration: "5m", target: 50 },
            { duration: "30s", target: 0 },
          ],
          gracefulRampDown: "15s",
        },
        runner: {
          executor: "constant-vus",
          exec: "runner",
          vus: 20,
          duration: "6m",
          startTime: "30s",
        },
      },
      thresholds: {
        http_req_failed: ["rate<0.01"],
        checks: ["rate>0.99"],
        "http_req_duration{name:GET /candidates/search}": ["p(95)<300"],
        "http_req_duration{name:POST /needs/{id}/search}": ["p(95)<500"],
        "http_req_duration{name:GET /candidates/{id}/card}": ["p(95)<200"],
        "http_req_duration{name:POST /tests/sessions/{id}/answer}": ["p(95)<300"],
      },
    };

function params(token, name, extra) {
  return {
    headers: Object.assign(
      { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      extra || {},
    ),
    tags: { name },
  };
}

export function setup() {
  if (!EMPLOYER_TOKEN) {
    throw new Error("EMPLOYER_TOKEN не задан — access token работодателя (см. шапку скрипта)");
  }
  const health = http.get(`${BASE}/api/v1/health/ready`, { tags: { name: "GET /health/ready" } });
  if (health.status !== 200) {
    throw new Error(`backend не готов (${health.status}): make dev && make seed`);
  }
  // потребность для подборки: создаём одну на весь прогон (идемпотентно для стенда)
  let needId = __ENV.NEED_ID || "";
  if (!needId) {
    const res = http.post(
      `${BASE}/api/v1/needs`,
      JSON.stringify({
        title: "Нагрузочный прогон k6",
        raw_text: "Бэкенд-разработчик уровня middle: Python, PostgreSQL, API, тесты.",
        specialization: SPEC,
        grade: GRADE,
      }),
      params(EMPLOYER_TOKEN, "POST /needs"),
    );
    if (res.status === 200 || res.status === 201) {
      needId = res.json("id");
    }
  }
  return { needId };
}

// Работодатель: поиск → карточка → (каждый 5-й VU) новая версия подборки
export function search(data) {
  let r = http.get(
    `${BASE}/api/v1/candidates/search?specialization=${SPEC}&grade=${GRADE}&limit=20`,
    params(EMPLOYER_TOKEN, "GET /candidates/search"),
  );
  check(r, { "search 200": (x) => x.status === 200 });
  const items = r.status === 200 ? r.json("items") || [] : [];
  sleep(1 + Math.random() * 2);

  if (items.length > 0) {
    const id = items[(__VU + __ITER) % items.length].id;
    const q = data.needId ? `?need_id=${data.needId}` : "";
    r = http.get(
      `${BASE}/api/v1/candidates/${id}/card${q}`,
      params(EMPLOYER_TOKEN, "GET /candidates/{id}/card"),
    );
    check(r, { "card 200": (x) => x.status === 200 });
    sleep(1 + Math.random() * 2);
  }

  if (data.needId && __VU % 5 === 0) {
    r = http.post(
      `${BASE}/api/v1/needs/${data.needId}/search`,
      JSON.stringify({ filters: {} }),
      params(EMPLOYER_TOKEN, "POST /needs/{id}/search"),
    );
    check(r, { "need search 200/201": (x) => x.status === 200 || x.status === 201 });
  }
  sleep(3 + Math.random() * 5);
}

// Кандидат: сессия демо-теста → цикл «текущее задание → ответ» → завершение
export function runner() {
  if (!CANDIDATE_TOKEN) {
    sleep(5);
    return;
  }
  let r = http.post(
    `${BASE}/api/v1/tests/sessions`,
    JSON.stringify({ specialization: SPEC, target_grade: GRADE }),
    params(CANDIDATE_TOKEN, "POST /tests/sessions"),
  );
  // 409/429 — cooldown или уже идущая сессия: легитимный отказ, не ошибка стенда
  if (r.status !== 200 && r.status !== 201) {
    check(r, { "session 409/429": (x) => x.status === 409 || x.status === 429 });
    sleep(10);
    return;
  }
  const sessionId = r.json("id");
  for (let i = 0; i < 12; i += 1) {
    r = http.get(
      `${BASE}/api/v1/tests/sessions/${sessionId}/current`,
      params(CANDIDATE_TOKEN, "GET /tests/sessions/{id}/current"),
    );
    if (r.status !== 200 || !r.json("item")) {
      break;
    }
    sleep(2 + Math.random() * 3); // «думает»
    r = http.post(
      `${BASE}/api/v1/tests/sessions/${sessionId}/answer`,
      JSON.stringify({ item_id: r.json("item.id"), answer: { text: "ответ нагрузочного прогона" } }),
      params(CANDIDATE_TOKEN, "POST /tests/sessions/{id}/answer"),
    );
    check(r, { "answer 202": (x) => x.status === 202 || x.status === 200 });
  }
  r = http.post(
    `${BASE}/api/v1/tests/sessions/${sessionId}/finish`,
    null,
    params(CANDIDATE_TOKEN, "POST /tests/sessions/{id}/finish"),
  );
  check(r, { "finish 200/202": (x) => x.status === 200 || x.status === 202 });
  sleep(5);
}
