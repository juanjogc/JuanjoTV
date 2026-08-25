# Route-by-route live validation

All commands run from `/Users/juanjoguzmanc/Documents/ChatGPT/UHF`. Each check verifies manifest → media playlist → current segment bytes and refuses a mislabeled route when the observed public country does not match.

## Peru direct — all 33

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --network-route DIRECT --network-label "Peru direct" \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health.md
```

## NordVPN France — ARTE

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel arte-fr --network-route FR \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-france.md
```

## NordVPN Belgium — LN24

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel ln24 --network-route BE \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-belgium.md
```

## NordVPN Netherlands — NPO Politiek en Nieuws

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel npo-politiek --network-route NL \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-netherlands.md
```

## NordVPN Germany — phoenix

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel phoenix --network-route DE \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-germany.md
```

## NordVPN Switzerland — RTS Info

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel rts-info --network-route CH \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-switzerland.md
```

## Approved preferred routes that also pass directly

```sh
python3 international-tv-hub/scripts/build_hub.py \
  --check --channel rtve-24h --network-route ES \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-spain.md

python3 international-tv-hub/scripts/build_hub.py \
  --check --channel rai-news-24 --network-route IT \
  --verify-egress-country \
  --report international-tv-hub/reports/source-health-italy.md
```

The Mac App Store NordVPN build is switched manually. The validation command independently checks the public country, so merely labeling a run `NL` or `BE` cannot create false evidence.
