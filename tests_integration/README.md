# Tests of the Sunflow integration

They use Home Assistant's own test helpers, so they run inside a Home Assistant core
checkout with its development environment:

```bash
ln -s "$PWD/custom_components/sunflow_ems" <core>/tests/testing_config/custom_components/sunflow_ems_ems
cp tests_integration/test_sunflow.py <core>/tests/test_sunflow_tmp.py
cd <core> && uv run --no-sync pytest tests/test_sunflow_tmp.py
rm <core>/tests/test_sunflow_tmp.py <core>/tests/testing_config/custom_components/sunflow_ems_ems
```
