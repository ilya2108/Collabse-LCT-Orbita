// Служебный скрипт keycloak-js для silent check-sso: сообщает родительскому окну URL iframe
// (в нём — код авторизации или ошибка). Отдельный файл — ради CSP script-src 'self'.
parent.postMessage(location.href, location.origin);
