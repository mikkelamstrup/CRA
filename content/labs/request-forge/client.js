let csrf = '';
const status = document.querySelector('#status');
async function send(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Forespørgslen fejlede');
  return data;
}
document.querySelector('#login').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const data = await send('/api/login', Object.fromEntries(new FormData(event.target)));
    csrf = data.csrf;
    document.querySelector('#search').hidden = false;
    status.textContent = 'Logget ind. Du kan nu kontrollere en ordre.';
  } catch (error) { status.textContent = error.message; }
});
document.querySelector('#search').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const data = await send('/api/search', Object.fromEntries(new FormData(event.target)));
    status.textContent = data.matched ? 'Ordren findes.' : 'Ingen match.';
  } catch (error) { status.textContent = error.message; }
});
