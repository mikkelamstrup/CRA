# M2-02 · Terminalprotokol og procesgrænser

Nginx i kontrol-VM ejer port 8000, videresender HTTP til API på loopback 8002 og `/terminal/{lab_uuid}` til separat relay på loopback 8001. Værtens eksisterende portforward 28000 er uændret. `/internal/` afvises ved kanten; alle interne terminalkald kræver stadig session, CSRF og tilladt Origin. Relay kører som `crarelay` uden sudo, PostgreSQL-adgang, API-signing key eller lifecycle-klientcertifikat. Systemd begrænser relay til 384 MiB/64 tasks og gør systemfiler readonly. Der er højst otte samtidige forbindelser.

API udsteder en 30-sekunders tilfældig engangsgrant bundet til session, bruger, organisation, lab og generation. Kun hash lagres. En ny grant tilbagekalder den tidligere for samme lab. WebSocket får grant og CSRF i første tekstframe; credentials findes ikke i URL eller protokollogs. Korrekt Cookie og Origin kræves også. En database-rækkelås sikrer engangsindløsning. API returnerer kun til relay en HMAC-signeret allocation med 15 sekunders udløb. Node kontrollerer signatur, ejerskab, faktisk domæne og generation og journalfører engangsbrug. Det særskilte `cra-terminal` mTLS-certifikat kan kun bruge allocation-endpointet, ikke inventory eller lifecycle. `cra-control` kan ikke hente SSH-materiale.

Relay modtager den konkrete operators projektnøgle og host-public-key i krypteret nodekommunikation og holder dem kun i hukommelsen. Browseren modtager ingen SSH-nøgler. AsyncSSH bruger faste host/port-valg, eksplicitte client keys og host-key-verifikation; ingen ambient SSH-config, agent, forwarding eller vilkårlig hostkommando. Den faste fjernkommando åbner brugerens generationsbundne tmux-session i egen operator-VM. Stop bevarer disk, men ikke kørende shell; netværksdisconnect bevarer tmux. Reset genskaber VM og lukker gammel generation i M2-03.

## Frames

- Første tekstframe: `auth` med grant, CSRF, cols og rows; højst 2048 tegn og fem sekunders deadline.
- Serverens `ready`: sessionen er autentificeret og PTY startet.
- Binære frames: uændrede terminalbytes. Input højst 16384 bytes per frame; ingen automatisk genafsendelse ved reconnect.
- Tekst `resize`: cols 10–240 og rows 2–100, heltal.
- Tekst `ack`: kumulativt antal outputbytes, der er behandlet af xterm.write-callback. ACK må hverken gå baglæns eller overstige sendt output.

Kontrolframes er højst 256 tegn. Output læses i højst 16 KiB-chunks og stopper ved 64 KiB ubekræftede bytes. SSH receive-window er også 64 KiB. WebSocket-køen rummer højst fire frames, og input har særskilt kø med fire chunks. En fuld inputkø lukker forbindelsen, så inputdrain ikke blokerer ACK/revokation. xterm har 3000 linjers lokal scrollback; terminalen er ikke en fuld logoptagelse. Filer med store resultater kan gemmes i gæsten.

Relay genvaliderer login, aktivt medlemskab, labtilstand og generation hvert andet sekund; kontrolkald har fem sekunders timeout og fejl lukker forbindelsen. Dermed kan en utilgængelig kontrolservice give op til cirka syv sekunders revokationsforsinkelse. UI viser forbindelsesstatus og genforbinder kun med ny grant. Det genafspiller ikke tidligere input. En ny SSH-PTY tilkobles samme tmux-session; sessionsindhold er terminalens aktuelle skærm og scrollback, ikke præcis replay af alle netværksbytes.

Ingen terminalbytes eller rå undtagelser logges. Drift kan se en kontrolleret exception-type, ikke dens indhold. Screenshots/accepttestdata er syntetiske. Grafiske CSS-regler fra xterm kræver dynamiske styles; CSP tillader inline CSS, mens scripts fortsat kun må komme fra eget origin, og frames/objects er afvist.
