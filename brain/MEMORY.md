# MEMORY.md — FloodClaims Pro Deployment Knowledge

## Business
- Company: Liberty Emporium
- Owner: Jay Alexander (Ronald J. Alexander Jr.)
- Address: 125 W Swannanoa Av, Liberty NC 27298
- Email: leprograms@protonmail.com
- Phone: 743-337-9506
- Website: https://alexanderai.site
- GitHub: https://github.com/Liberty-Emporium

## Deployment
- Primary: https://flood-claims.alexanderai.site (Railway)
- Database: SQLite on Railway volume (/data/floodclaim.db)
- AI: OpenRouter (OPENROUTER_API_KEY env var)
- Session: 30-day cookie, server-side

## Integrations
- Stripe: payments (Basic $49, Pro $149, Agency $249/mo)
- SendGrid: email delivery
- Twilio: SMS notifications
- FEMA NFHL API: flood zone lookup
- Census Geocoding: address geocoding
- Xactimate: export format support

## Agent System
- Willie Agent ID: F5J8yYT6a6GrppjviN6p8w
- Multi-agent: OWL (Kali) + Bull (KiloClaw)
- Chat model: locked to openrouter/OWL Alpha
- Vision model: configurable in Settings → Vision Model

## Water Classification
- Category 1: Clean Water (sanitary — broken supply line, sink/tub overflow)
- Category 2: Gray Water (significant contamination — sump backup, washing machine overflow)
- Category 3: Black Water (grossly contaminated — sewage, floodwater, river water)
- Class 1: Affects only part of room, minimal absorption
- Class 2: Affects entire room, carpet and padding, wicking up walls 24-48"
- Class 3: Fastest evaporation rate, ceilings and walls saturated
- Class 4: Specialty drying — hardwood, concrete, plaster

## NFIP Policy Limits
- Residential: Building $250,000, Contents $100,000
- Commercial: Building $500,000, Contents $500,000
- Deductibles: $1,000-$10,000 depending on zone and elevation
- Proof of Loss: Required within 60 days of loss date (unless extended by FEMA)

## Roles
- Admin: full access, settings, team, recruit, analytics, billing
- Adjuster: assigned claims only, create/edit own claims, view own inspections

## Routes
/ (dashboard), /new_claim, /claims/<id>, /pipeline, /schedule, /notifications, /analytics, /billing, /admin/settings, /admin/team, /admin/recruit, /willie, /portal/<token>, /login, /logout, /health
