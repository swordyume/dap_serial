#ifndef SERIALPRO_CHASSIS_SERIAL_H
#define SERIALPRO_CHASSIS_SERIAL_H

#include <cstddef>
#include <cstdint>
#include <utility>

#include "serialPro.h"

namespace chassis {
    message_data Vec3 {
        float x;
        float y;
        float z;
    };

    message_data ChassisData {
        Vec3 position;
        Vec3 velocity;
        Vec3 imu_acceleration;
        Vec3 board_position[4];
        Vec3 board_velocity[4];
        float board_radius[4];
        float board_angle[4];
        float motor_6020_angular_velocity;
        float angular_acceleration;
    };

    message_data head {
        uint8_t header1 = 0xAF;
        uint8_t header2 = 0xFA;
    };

    message_data tail {
        uint8_t crc8 = 0;
    };

    static_assert(sizeof(Vec3) == 12, "Vec3 must be 12 bytes");
    static_assert(sizeof(ChassisData) == 172, "ChassisData must be 172 bytes");
    static_assert(sizeof(head) == 2, "head must be 2 bytes");
    static_assert(sizeof(tail) == 1, "tail must be 1 byte");
    static_assert(sizeof(head) + sizeof(ChassisData) + sizeof(tail) == 175,
                  "chassis frame must be 175 bytes");

    inline uint8_t crc8(const uint8_t *data, std::size_t length) {
        uint8_t crc = 0x00;
        for (std::size_t i = 0; i < length; ++i) {
            crc ^= data[i];
            for (int bit = 0; bit < 8; ++bit) {
                if (crc & 0x80) {
                    crc = static_cast<uint8_t>((crc << 1) ^ 0x07);
                } else {
                    crc = static_cast<uint8_t>(crc << 1);
                }
            }
        }
        return crc;
    }

    class ChassisSerial : public sp::serialPro<head, tail> {
    public:
        enum error {
            lengthNotMatch = -2,
            rxLessThanLength = -1,
            ok = 0,
            sofError,
            crcError
        };

        ChassisSerial() = default;

        ChassisSerial(const std::string &port, int baud)
                : sp::serialPro<head, tail>(port, baud) {
            registerChecker([](const head &h) -> int {
                if (h.header1 == 0xAF && h.header2 == 0xFA) {
                    return ok;
                }
                return sofError;
            });
            registerChecker([](const tail &t, const uint8_t *data, int length) -> int {
                if (t.crc8 == crc8(data, static_cast<std::size_t>(length))) {
                    return ok;
                }
                return crcError;
            });
            setGetLength([](const head &) -> std::size_t {
                return sizeof(ChassisData);
            });
            setGetId([](const head &) -> int {
                return 0;
            });
            setListenerMaxSize(1024);
        }

        ChassisSerial(const ChassisSerial &) = delete;

        ChassisSerial &operator=(const ChassisSerial &) = delete;

        ChassisSerial(ChassisSerial &&other) noexcept
                : sp::serialPro<head, tail>(std::move(other)) {}

        using sp::serialPro<head, tail>::operator=;

        template<typename T>
        void registerCallback(T callback) {
            sp::serialPro<head, tail>::registerCallback(0, std::forward<T>(callback));
        }
    };
}

#endif //SERIALPRO_CHASSIS_SERIAL_H
