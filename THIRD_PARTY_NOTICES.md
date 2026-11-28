# Third-party notices

Sunflow is licensed under the Apache License 2.0 (see `LICENSE`). It contains or is derived
from the following works of others, under their licenses.

## Code included in this repository

### Lit (`frontend/vendor/lit-all.min.js`)

The Lit web components library, bundled unchanged.

```text
BSD 3-Clause License

Copyright (c) The Lit Project Contributors.
Copyright (c) 2017 Google LLC. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### Sunflow EEBUS App (`eebus/`, `sunflow_eebus/`)

The EEBUS service is a separate program built on these Go modules, linked into it unchanged:
enbility's eebus-go, ship-go and spine-go (MIT License, Copyright (c) 2022 Andreas Linde &
Timo Vogel) and their dependencies (MIT, BSD and Apache-2.0 licences), plus the Go standard
library (BSD-3-Clause). The full licence texts of every linked module are in
`eebus/THIRD_PARTY_LICENSES.txt` (regenerate with `python3 tools/eebus_licences.py` after
changing `eebus/go.mod`); the App image ships them.

## Facts taken from other projects

No code of these projects is included. Profiles name facts about devices and integrations
(register addresses, data types, scales, meanings of values, entity keys, option and action
names), which are taken from their documentation or source code:

- **evcc** (https://github.com/evcc-io/evcc, MIT License): register maps of the wallboxes in
  `profiles/loadpoints/direct/` and of `profiles/inverters/direct/victron_gx.yaml`.
- **Home Assistant integrations** named in each profile's header comment: entity keys, units,
  states, options and actions.
