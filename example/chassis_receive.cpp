#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <string>

#include "serialPro/chassisSerial.h"

namespace {
    void printVec(const char *name, const chassis::Vec3 &v) {
        std::cout << name << "=(" << v.x << ", " << v.y << ", " << v.z << ") ";
    }

    void printData(std::uint64_t count, const chassis::ChassisData &data) {
        std::cout << "frame " << count << '\n';
        printVec("position", data.position);
        printVec("velocity", data.velocity);
        printVec("imu_acceleration", data.imu_acceleration);
        std::cout << '\n';
        for (int i = 0; i < 4; ++i) {
            std::cout << "board" << i << " ";
            printVec("position", data.board_position[i]);
            printVec("velocity", data.board_velocity[i]);
            std::cout << "radius=" << data.board_radius[i]
                      << " angle=" << data.board_angle[i] << '\n';
        }
        std::cout << "motor_6020_angular_velocity=" << data.motor_6020_angular_velocity
                  << " angular_acceleration=" << data.angular_acceleration << std::endl;
    }
}

int main(int argc, char **argv) {
    const std::string port = argc > 1 ? argv[1] : "/dev/ttyACM0";
    const int baud = argc > 2 ? std::stoi(argv[2]) : 115200;

    try {
        chassis::ChassisSerial serial(port, baud);
        serial.registerCallback([](const chassis::ChassisData &data) {
            static std::uint64_t frameCount = 0;
            static auto lastPrint =
                    std::chrono::steady_clock::now() - std::chrono::seconds(1);
            ++frameCount;
            const auto now = std::chrono::steady_clock::now();
            if (now - lastPrint < std::chrono::seconds(1)) {
                return;
            }
            lastPrint = now;
            printData(frameCount, data);
        });
        std::cout << "listening on " << port << " at " << baud << '\n';
        std::cout << std::fixed << std::setprecision(4);
        serial.spin(false);
    } catch (const std::exception &e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
