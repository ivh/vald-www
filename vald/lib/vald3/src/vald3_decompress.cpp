#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>
#include <vector>
#include <string>
#include <memory>
#include <cstring>
#include <algorithm>

// Include the original C decompression code
extern "C" {
    // Forward declarations from unkompress3.c
    int ukopen_(int *ifile, char *file_data, char *file_descr);
    int ukclose_(int *ifile);
    int ukread_(int *ifile, double *ptwave1, double *ptwave2,
                double *wl, int *element,
                float *j_low, double *e_low, float *j_high, double *e_high,
                float *loggf, float *gamrad, float *gamst,
                float *gamvw, float *lande_low, float *lande_high,
                char *str);
    int uknext_(int *ifile, double *wl, int *element,
                float *j_low, double *e_low, float *j_high, double *e_high,
                float *loggf, float *gamrad, float *gamst,
                float *gamvw, float *lande_low, float *lande_high,
                char *str);
}

namespace nb = nanobind;
using namespace nb::literals;

class VALD3Reader {
private:
    int file_handle;
    bool is_open;
    std::string data_file;
    std::string desc_file;

    // Internal buffer size (matches LINES_PER_RECORD in C code)
    static const int RECORD_SIZE = 1024;
    static const int STR_BYTES_PER_LINE = 210;

public:
    VALD3Reader(const char* data_filename, const char* desc_filename)
        : file_handle(1), is_open(false), data_file(data_filename), desc_file(desc_filename) {
        open();
    }

    ~VALD3Reader() {
        close();
    }

    void open() {
        if (is_open) return;

        // Prepare filenames as expected by ukopen_ (with trailing spaces)
        std::string data_with_space = data_file + " ";
        std::string desc_with_space = desc_file + " ";

        // Convert to char arrays for C interface
        std::vector<char> data_buf(data_with_space.size() + 1);
        std::vector<char> desc_buf(desc_with_space.size() + 1);

        strcpy(data_buf.data(), data_with_space.c_str());
        strcpy(desc_buf.data(), desc_with_space.c_str());

        int result = ukopen_(&file_handle, data_buf.data(), desc_buf.data());
        if (result < 0) {
            throw std::runtime_error("Failed to open VALD3 files. Error code: " + std::to_string(result));
        }
        is_open = true;
    }

    void close() {
        if (is_open) {
            ukclose_(&file_handle);
            is_open = false;
        }
    }

    int test_simple() {
        return 42;  // Simple test function
    }

    nb::dict query_range(double wl_min, double wl_max, int max_lines = 100000) {
        if (!is_open) {
            throw std::runtime_error("VALD3 file not open");
        }

        if (wl_min >= wl_max) {
            throw std::runtime_error("Invalid wavelength range");
        }

        // Allocate output arrays with capacity for max_lines
        std::vector<double> wl_out;
        std::vector<int> element_out;
        std::vector<float> j_low_out, j_high_out;
        std::vector<double> e_low_out, e_high_out;
        std::vector<float> loggf_out;
        std::vector<float> gamrad_out, gamst_out, gamvw_out;
        std::vector<float> lande_low_out, lande_high_out;
        std::vector<char> str_out;

        // Reserve space
        wl_out.reserve(max_lines);
        element_out.reserve(max_lines);
        j_low_out.reserve(max_lines);
        j_high_out.reserve(max_lines);
        e_low_out.reserve(max_lines);
        e_high_out.reserve(max_lines);
        loggf_out.reserve(max_lines);
        gamrad_out.reserve(max_lines);
        gamst_out.reserve(max_lines);
        gamvw_out.reserve(max_lines);
        lande_low_out.reserve(max_lines);
        lande_high_out.reserve(max_lines);
        str_out.reserve(max_lines * STR_BYTES_PER_LINE);

        // Temporary buffers for reading one record at a time
        std::vector<double> wl(RECORD_SIZE);
        std::vector<int> element(RECORD_SIZE);
        std::vector<float> j_low(RECORD_SIZE), j_high(RECORD_SIZE);
        std::vector<double> e_low(RECORD_SIZE), e_high(RECORD_SIZE);
        std::vector<float> loggf(RECORD_SIZE);
        std::vector<float> gamrad(RECORD_SIZE), gamst(RECORD_SIZE), gamvw(RECORD_SIZE);
        std::vector<float> lande_low(RECORD_SIZE), lande_high(RECORD_SIZE);
        std::vector<char> str(RECORD_SIZE * STR_BYTES_PER_LINE);

        int total_lines = 0;

        // Read first record with ukread_ (positions at first matching record)
        int nlines = ukread_(&file_handle, &wl_min, &wl_max,
                            wl.data(), element.data(),
                            j_low.data(), e_low.data(), j_high.data(), e_high.data(),
                            loggf.data(), gamrad.data(), gamst.data(), gamvw.data(),
                            lande_low.data(), lande_high.data(),
                            str.data());

        // Process records until we hit end or reach max_lines
        while (nlines > 0 && total_lines < max_lines) {
            // Filter and copy lines that are in wavelength range
            for (int i = 0; i < nlines && total_lines < max_lines; i++) {
                if (wl[i] >= wl_min && wl[i] <= wl_max) {
                    wl_out.push_back(wl[i]);
                    element_out.push_back(element[i]);
                    j_low_out.push_back(j_low[i]);
                    j_high_out.push_back(j_high[i]);
                    e_low_out.push_back(e_low[i]);
                    e_high_out.push_back(e_high[i]);
                    loggf_out.push_back(loggf[i]);
                    gamrad_out.push_back(gamrad[i]);
                    gamst_out.push_back(gamst[i]);
                    gamvw_out.push_back(gamvw[i]);
                    lande_low_out.push_back(lande_low[i]);
                    lande_high_out.push_back(lande_high[i]);

                    // Copy string data
                    for (int j = 0; j < STR_BYTES_PER_LINE; j++) {
                        str_out.push_back(str[i * STR_BYTES_PER_LINE + j]);
                    }
                    total_lines++;
                }
            }

            // Read next record
            nlines = uknext_(&file_handle, 
                            wl.data(), element.data(),
                            j_low.data(), e_low.data(), j_high.data(), e_high.data(),
                            loggf.data(), gamrad.data(), gamst.data(), gamvw.data(),
                            lande_low.data(), lande_high.data(),
                            str.data());

            // Check if we've passed the wavelength range
            // (records are sorted by wavelength, so if first line of record is > wl_max, we're done)
            if (nlines > 0 && wl[0] > wl_max) {
                break;
            }
        }

        // Create result dictionary
        nb::dict result;
        result["nlines"] = total_lines;

        if (total_lines > 0) {
            // Resize vectors to actual size
            wl_out.resize(total_lines);
            element_out.resize(total_lines);
            loggf_out.resize(total_lines);
            e_low_out.resize(total_lines);
            e_high_out.resize(total_lines);
            j_low_out.resize(total_lines);
            j_high_out.resize(total_lines);
            lande_low_out.resize(total_lines);
            lande_high_out.resize(total_lines);
            gamrad_out.resize(total_lines);
            gamst_out.resize(total_lines);
            gamvw_out.resize(total_lines);
            
            // Create numpy arrays that own copies of the data
            result["wavelength_vacuum"] = nb::cast(wl_out, nb::rv_policy::copy);
            result["species_code"] = nb::cast(element_out, nb::rv_policy::copy);
            result["loggf"] = nb::cast(loggf_out, nb::rv_policy::copy);
            result["e_lower"] = nb::cast(e_low_out, nb::rv_policy::copy);
            result["e_upper"] = nb::cast(e_high_out, nb::rv_policy::copy);
            result["j_lower"] = nb::cast(j_low_out, nb::rv_policy::copy);
            result["j_upper"] = nb::cast(j_high_out, nb::rv_policy::copy);
            result["lande_lower"] = nb::cast(lande_low_out, nb::rv_policy::copy);
            result["lande_upper"] = nb::cast(lande_high_out, nb::rv_policy::copy);
            result["gamma_rad"] = nb::cast(gamrad_out, nb::rv_policy::copy);
            result["gamma_stark"] = nb::cast(gamst_out, nb::rv_policy::copy);
            result["gamma_vdw"] = nb::cast(gamvw_out, nb::rv_policy::copy);
            
            // String data as bytes
            str_out.resize(static_cast<size_t>(total_lines) * STR_BYTES_PER_LINE);
            result["string_data"] = nb::bytes(str_out.data(), str_out.size());
        }

        return result;
    }

    bool is_file_open() const {
        return is_open;
    }
};

NB_MODULE(vald3_decompress, m) {
    m.doc() = "VALD3 decompression wrapper using nanobind";

    nb::class_<VALD3Reader>(m, "VALD3Reader")
        .def(nb::init<const char*, const char*>(),
             "data_file"_a, "desc_file"_a,
             "Initialize VALD3Reader with compressed data and descriptor files")
        .def("test_simple", &VALD3Reader::test_simple,
             "Simple test function")
        .def("query_range", &VALD3Reader::query_range,
             "wl_min"_a, "wl_max"_a, "max_lines"_a = 100000,
             "Query spectral lines in wavelength range [wl_min, wl_max] - returns dict with arrays")
        .def("is_open", &VALD3Reader::is_file_open,
             "Check if the VALD3 file is open")
        .def("close", &VALD3Reader::close,
             "Close the VALD3 file");
}