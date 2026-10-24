import './style.css';
import { Store } from './store';
import { startMock } from './mock';
import { mountViewer } from './viewer/scene';

const store = new Store();
const app = document.querySelector<HTMLDivElement>('#app')!;
app.innerHTML = `<main class="layout"><section id="viewer" class="viewer"></section><aside id="panels" class="panels"></aside></main>`;
mountViewer(document.getElementById('viewer')!, store, () => {});
if (new URLSearchParams(location.search).has('mock')) startMock(store);
