// ============================================================================
//  The Siege of Grimgate - Cloudflare Worker : echange OAuth Discord.
//
//  Le SEUL role de ce serveur : recevoir le "code" d'autorisation renvoye par
//  Discord, l'echanger contre un jeton (avec le CLIENT SECRET, qui reste ICI et
//  jamais dans le jeu), puis renvoyer au jeu UNIQUEMENT l'identite du joueur
//  (id + pseudo Discord). Le jeton n'est jamais renvoye au client.
//
//  Variables a definir dans Cloudflare (Settings > Variables and Secrets) :
//    DISCORD_CLIENT_ID      (texte)   = l'Application ID de ton appli Discord
//    DISCORD_CLIENT_SECRET  (secret)  = le Client Secret de ton appli Discord
//    REDIRECT_URI           (texte)   = http://localhost:53127/callback
// ============================================================================

export default {
  async fetch(request, env) {
    if (request.method === "GET") {
      return json({ ok: true, service: "tsog-discord-auth" });   // ping de sante
    }
    if (request.method !== "POST") {
      return json({ error: "method_not_allowed" }, 405);
    }
    let body;
    try {
      body = await request.json();
    } catch (e) {
      return json({ error: "bad_json" }, 400);
    }
    if (!body || !body.code) {
      return json({ error: "no_code" }, 400);
    }

    // 1) Echange du code contre un jeton (le secret reste cote serveur).
    const tokenRes = await fetch("https://discord.com/api/oauth2/token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        client_id: env.DISCORD_CLIENT_ID,
        client_secret: env.DISCORD_CLIENT_SECRET,
        grant_type: "authorization_code",
        code: body.code,
        redirect_uri: body.redirect_uri || env.REDIRECT_URI,
      }),
    });
    if (!tokenRes.ok) {
      return json({ error: "token_exchange_failed", status: tokenRes.status }, 400);
    }
    const tok = await tokenRes.json();

    // 2) Recupere l'identite du joueur.
    const userRes = await fetch("https://discord.com/api/users/@me", {
      headers: { Authorization: "Bearer " + tok.access_token },
    });
    if (!userRes.ok) {
      return json({ error: "user_fetch_failed", status: userRes.status }, 400);
    }
    const u = await userRes.json();

    // 3) Ne renvoie QUE l'identite (jamais le jeton). 'avatar' = hash pour la photo de profil.
    return json({
      id: u.id,
      username: u.username,
      global_name: u.global_name || null,
      avatar: u.avatar || null,
    });
  },
};

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}
