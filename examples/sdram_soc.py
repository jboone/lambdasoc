import argparse

from nmigen import *
from nmigen.build import *
from nmigen_soc import wishbone

from lambdasoc.cpu.minerva import MinervaCPU
from lambdasoc.periph.intc import GenericInterruptController
from lambdasoc.periph.serial import AsyncSerialPeripheral
from lambdasoc.periph.sram import SRAMPeripheral
from lambdasoc.periph.timer import TimerPeripheral
from lambdasoc.periph.sdram import SDRAMPeripheral
from lambdasoc.soc.cpu import CPUSoC

from lambdasoc.cores import litedram


__all__ = ["SDRAMSoC"]


class SDRAMSoC(CPUSoC, Elaboratable):
    def __init__(self, *, reset_addr, clk_freq,
                 rom_addr, rom_size,
                 ram_addr, ram_size,
                 uart_addr, uart_divisor, uart_pins,
                 timer_addr, timer_width,
                 sdram_addr, sdram_core, sdram_cache_size):
        self._arbiter = wishbone.Arbiter(addr_width=30, data_width=32, granularity=8,
                                         features={"cti", "bte"})
        self._decoder = wishbone.Decoder(addr_width=30, data_width=32, granularity=8,
                                         features={"cti", "bte"})

        self.cpu = MinervaCPU(
            reset_address=reset_addr,
            with_icache=True, icache_nlines=128, icache_nwords=4, icache_nways=1,
                              icache_base=sdram_addr, icache_limit=sdram_addr + sdram_core.size,
            with_dcache=True, dcache_nlines=128, dcache_nwords=4, dcache_nways=1,
                              dcache_base=sdram_addr, dcache_limit=sdram_addr + sdram_core.size,
            with_muldiv=True,
        )
        self._arbiter.add(self.cpu.ibus)
        self._arbiter.add(self.cpu.dbus)

        self.rom = SRAMPeripheral(size=rom_size, writable=False)
        self._decoder.add(self.rom.bus, addr=rom_addr)

        self.ram = SRAMPeripheral(size=ram_size)
        self._decoder.add(self.ram.bus, addr=ram_addr)

        self.sdram = SDRAMPeripheral(core=sdram_core, cache_size=sdram_cache_size)
        self._decoder.add(self.sdram.bus, addr=sdram_addr)

        self.uart = AsyncSerialPeripheral(divisor=uart_divisor, pins=uart_pins)
        self._decoder.add(self.uart.bus, addr=uart_addr)

        self.timer = TimerPeripheral(width=timer_width)
        self._decoder.add(self.timer.bus, addr=timer_addr)

        self.intc = GenericInterruptController(width=len(self.cpu.ip))
        self.intc.add_irq(self.timer.irq, 0)
        self.intc.add_irq(self.uart .irq, 1)

        self.memory_map = self._decoder.bus.memory_map

        self.clk_freq = clk_freq

    def elaborate(self, platform):
        m = Module()

        m.domains += [
            ClockDomain("litedram_input"),
            ClockDomain("litedram_user"),
            ClockDomain("sync"),
        ]

        m.d.comb += [
            ClockSignal("litedram_input").eq(platform.request("clk100", 0).i),

            ClockSignal("sync").eq(ClockSignal("litedram_user")),
            ResetSignal("sync").eq(ResetSignal("litedram_user")),
        ]

        m.submodules.arbiter = self._arbiter
        m.submodules.cpu     = self.cpu

        m.submodules.decoder = self._decoder
        m.submodules.rom     = self.rom
        m.submodules.ram     = self.ram
        m.submodules.sdram   = self.sdram
        m.submodules.uart    = self.uart
        m.submodules.timer   = self.timer
        m.submodules.intc    = self.intc

        m.d.comb += [
            self._arbiter.bus.connect(self._decoder.bus),
            self.cpu.ip.eq(self.intc.ip),
        ]

        return m


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baudrate", type=int,
            default=9600,
            help="UART baudrate (default: 9600)")
    args = parser.parse_args()

    # from nmigen_boards.arty_a7 import ArtyA7_35Platform
    # platform = ArtyA7_35Platform()
    # litedram_cfg = litedram.Artix7Config(
    #     memtype          = "DDR3",
    #     speedgrade       = "-1",
    #     cmd_latency      = 0,
    #     module_name      = "MT41K128M16",
    #     module_bytes     = 2,
    #     module_ranks     = 1,
    #     rtt_nom          = 60,
    #     rtt_wr           = 60,
    #     ron              = 34,
    #     input_clk_freq   = int(100e6),
    #     user_clk_freq    = int(100e6),
    #     iodelay_clk_freq = int(200e6),
    # )

    from nmigen_boards.ecpix5 import ECPIX585Platform
    platform = ECPIX585Platform()
    litedram_cfg = litedram.ECP5Config(
        memtype        = "DDR3",
        module_name    = "MT41K256M16",
        module_bytes   = 2,
        module_ranks   = 1,
        input_clk_freq = int(100e6),
        user_clk_freq  = int(70e6),
        init_clk_freq  = int(25e6),
    )

    litedram_core = litedram.Core(
        litedram_cfg,
        pins = litedram_cfg.request_pins(platform, "ddr3", 0),
    )

    litedram_builder  = litedram.Builder()
    litedram_products = litedram_core.build(litedram_builder, do_build=True)

    litedram_core_v = f"{litedram_core.name}/{litedram_core.name}.v"
    platform.add_file(litedram_core_v, litedram_products.get(litedram_core_v, mode="t"))

    soc = SDRAMSoC(
         reset_addr=0x30000000, clk_freq=litedram_cfg.user_clk_freq,
          uart_addr=0x00005000, uart_divisor=int(litedram_cfg.user_clk_freq // args.baudrate),
                                uart_pins=platform.request("uart", 0),
         timer_addr=0x00006000, timer_width=32,

           rom_addr=0x30000000, rom_size=0x8000,
           ram_addr=0x30008000, ram_size=0x1000,
         sdram_addr=0x40000000, sdram_core=litedram_core, sdram_cache_size=8192,
    )
    soc.build(do_build=True, do_init=True)

    platform.build(soc, do_program=True)