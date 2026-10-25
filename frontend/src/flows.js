/**
 * Split measured totals into flows between PV, battery, grid and house.
 *
 * Signs: battery + = charging, grid + = import. PV covers the house first, then
 * charges the battery, the rest is exported; the battery covers the house before
 * it exports; grid import covers the house before it charges the battery.
 */
export function splitFlows({ pv = 0, battery = 0, grid = 0, load = null }) {
  const pvPower = Math.max(pv ?? 0, 0);
  const charge = Math.max(battery ?? 0, 0);
  const discharge = Math.max(-(battery ?? 0), 0);
  const imported = Math.max(grid ?? 0, 0);
  const exported = Math.max(-(grid ?? 0), 0);
  // Without a load sensor the house is what is left in the balance
  const house = load ?? Math.max(pvPower + discharge + imported - charge - exported, 0);

  let pvLeft = pvPower;
  let houseLeft = house;
  const pvToHome = Math.min(pvLeft, houseLeft);
  pvLeft -= pvToHome;
  houseLeft -= pvToHome;
  const pvToBattery = Math.min(pvLeft, charge);
  pvLeft -= pvToBattery;
  const pvToGrid = Math.min(pvLeft, exported);

  let dischargeLeft = discharge;
  const batteryToHome = Math.min(dischargeLeft, houseLeft);
  dischargeLeft -= batteryToHome;
  houseLeft -= batteryToHome;
  const batteryToGrid = Math.min(dischargeLeft, Math.max(exported - pvToGrid, 0));

  let importLeft = imported;
  const gridToHome = Math.min(importLeft, houseLeft);
  importLeft -= gridToHome;
  const gridToBattery = Math.min(importLeft, Math.max(charge - pvToBattery, 0));

  return { house, pvToHome, pvToBattery, pvToGrid, batteryToHome, batteryToGrid, gridToHome, gridToBattery };
}

/** Share of the consumption covered without the grid and share of PV used on site. */
export function dailyRatios({ pv, consumption, imported, exported }) {
  return {
    autarky: consumption > 0 ? Math.min(Math.max(1 - imported / consumption, 0), 1) : null,
    selfConsumption: pv > 0 ? Math.min(Math.max(1 - exported / pv, 0), 1) : null,
  };
}
